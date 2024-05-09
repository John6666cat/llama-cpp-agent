from inspect import isclass
from pydantic import BaseModel, Field
from enum import Enum
from typing import Union, get_origin, get_args, List, Set, Dict
from types import NoneType

from llama_cpp_agent.llm_documentation import generate_text_documentation


def custom_json_schema(model: BaseModel):
    def get_type_str(annotation):
        """Resolve the JSON type string from the Python annotation."""
        basic_types = {
            int: "integer",
            float: "number",
            str: "string",
            bool: "boolean",
            NoneType: "null",
        }
        return basic_types.get(annotation, None)

    def refine_schema(schema, model):
        """Refine the generated schema based on the model's annotations and field details."""
        if "properties" in schema:
            for name, prop in schema["properties"].items():
                field = model.__fields__[name]
                prop["title"] = name.replace("_", " ").title()
                prop["description"] = field.description or ""

                # Handle Enums
                if isclass(field.annotation) and issubclass(field.annotation, Enum):
                    prop.pop("allOf")
                    prop["enum"] = [e.value for e in field.annotation]
                    prop["type"] = get_type_str(
                        type(next(iter(field.annotation)).value)
                    )

                # Handle Unions, including Optional
                origin = get_origin(field.annotation)
                if origin is Union:
                    types = get_args(field.annotation)
                    new_anyof = []
                    for sub_type in types:
                        type_str = get_type_str(sub_type)
                        if sub_type is NoneType:
                            new_anyof.append({"type": type_str})
                        elif isclass(sub_type) and issubclass(sub_type, BaseModel):
                            new_anyof.append(refine_schema(sub_type.schema(), sub_type))
                        elif type_str:
                            new_anyof.append({"type": type_str})
                    prop["anyOf"] = new_anyof

                # Handle lists and sets containing Pydantic models or basic types
                elif origin in [list, set]:
                    item_type = get_args(field.annotation)[0]
                    if isclass(item_type) and issubclass(item_type, BaseModel):
                        prop["items"] = refine_schema(item_type.schema(), item_type)
                    else:
                        origin = get_origin(item_type)
                        if origin is Union:
                            types = get_args(item_type)
                            new_anyof = []
                            for sub_type in types:
                                type_str = get_type_str(sub_type)
                                if sub_type is NoneType:
                                    new_anyof.append({"type": type_str})
                                elif isclass(sub_type) and issubclass(
                                    sub_type, BaseModel
                                ):
                                    new_anyof.append(
                                        refine_schema(sub_type.schema(), sub_type)
                                    )
                                elif type_str:
                                    new_anyof.append({"type": type_str})
                            prop["items"]["anyOf"] = new_anyof
                        else:
                            type_str = get_type_str(item_type)
                            if type_str:
                                prop["items"] = {"type": type_str}

                # Handle dictionaries
                elif origin is dict:
                    key_type, value_type = get_args(field.annotation)
                    key_type_str = get_type_str(key_type)
                    if isclass(value_type) and issubclass(value_type, BaseModel):
                        prop["additionalProperties"] = refine_schema(
                            value_type.schema(), value_type
                        )
                    else:
                        value_type_str = get_type_str(value_type)
                        prop["additionalProperties"] = {"type": value_type_str}
                    prop["type"] = "object"

                # Handle nested Pydantic models
                elif isclass(field.annotation) and issubclass(
                    field.annotation, BaseModel
                ):
                    prop.update(
                        refine_schema(field.annotation.schema(), field.annotation)
                    )

        schema["title"] = model.__name__
        schema["description"] = model.__doc__.strip() if model.__doc__ else ""
        schema["required"] = [
            name for name, field in model.__fields__.items() if field.is_required
        ]
        if "$defs" in schema:
            schema.pop("$defs")
        return schema

    return refine_schema(model.schema(), model)


def generate_list(
    models: List[BaseModel],
    outer_object_name=None,
    outer_object_properties_name=None,
    add_inner_thoughts: bool = False,
    inner_thoughts_name: str = "thoughts_and_reasoning",
):
    list_object = {"type": "array", "items": {"type": "object", "anyOf": []}}

    for model in models:
        schema = custom_json_schema(model)
        outer_object = {}

        if outer_object_name is not None and outer_object_properties_name is not None:
            function_name_object = {"enum": [model.__name__], "type": "string"}
            model_schema_object = schema

            if add_inner_thoughts:
                # Create a wrapper object that contains the function name and the model schema
                wrapper_object = {
                    "type": "object",
                    "properties": {
                        "001_" + inner_thoughts_name: {"type": "string"},
                        "002_" + outer_object_name: function_name_object,
                        "003_" + outer_object_properties_name: model_schema_object,
                    },
                    "required": [
                        "001_" + inner_thoughts_name,
                        "002_" + outer_object_name,
                        "003_" + outer_object_properties_name,
                    ],
                }
            else:
                # Create a wrapper object that contains the function name and the model schema
                wrapper_object = {
                    "type": "object",
                    "properties": {
                        "001_" + outer_object_name: function_name_object,
                        "002_" + outer_object_properties_name: model_schema_object,
                    },
                    "required": [
                        "001_" + outer_object_name,
                        "002_" + outer_object_properties_name,
                    ],
                }

            outer_object.update(wrapper_object)
        else:
            outer_object = schema
        list_object["items"]["anyOf"].append(outer_object)
        list_object["minItems"] = 1
        list_object["maxItems"] = 10

    return list_object


def generate_json_schemas(
    models: List[BaseModel],
    outer_object_name=None,
    outer_object_properties_name=None,
    allow_list=False,
    add_inner_thoughts: bool = False,
    inner_thoughts_name: str = "thoughts_and_reasoning",
):
    if allow_list:
        model_schema_list = generate_list(
            models,
            outer_object_name,
            outer_object_properties_name,
            add_inner_thoughts,
            inner_thoughts_name,
        )
    else:
        model_schema_list = [custom_json_schema(model) for model in models]

    return model_schema_list
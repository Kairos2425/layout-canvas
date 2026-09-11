from layout_canvas.ir.model import (
    IR_VERSION,
    BlockSpec,
    Constraint,
    ConstraintType,
    Design,
    Instance,
    Net,
    ParamSpec,
    Placement,
    Port,
    PortSpec,
)


def json_schema() -> dict:
    """JSON Schema for the Block IR design document."""
    schema = Design.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://layout-canvas.dev/schema/block_ir_v{IR_VERSION}.json"
    return schema


__all__ = [
    "IR_VERSION",
    "BlockSpec",
    "Constraint",
    "ConstraintType",
    "Design",
    "Instance",
    "Net",
    "ParamSpec",
    "Placement",
    "Port",
    "PortSpec",
    "json_schema",
]

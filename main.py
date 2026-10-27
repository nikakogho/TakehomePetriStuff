from inspect_ai import eval

from config import GPT5

eval(
    "inspect_petri/audit",
    model_roles={"auditor": GPT5, "target": GPT5, "judge": GPT5},
)

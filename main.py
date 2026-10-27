from inspect_ai import eval

from config import GPT5

eval(
    "petri/audit",
    model_roles={"auditor": GPT5, "target": GPT5, "judge": GPT5},
)

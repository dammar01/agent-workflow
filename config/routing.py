from config.roles import ROLE_EXECUTION, ROLE_EXPLORATION, ROLE_REASONING, ROLE_VERIFICATION

COMMAND_ROUTES = {
    "init": {"role": ROLE_VERIFICATION, "model": None},
    "doctor": {"role": ROLE_VERIFICATION, "model": None},
    "explore": {"role": ROLE_EXPLORATION, "model": None},
    "plan": {"role": ROLE_REASONING, "model": None},
    "analyze": {"role": ROLE_REASONING, "model": None},
    "execute": {"role": ROLE_EXECUTION, "model": None},
    "verify": {"role": ROLE_VERIFICATION, "model": None},
    "sweep": {"role": ROLE_VERIFICATION, "model": None},
    "submit": {"role": ROLE_EXECUTION, "model": None},
    "status": {"role": ROLE_EXPLORATION, "model": None},
    "result": {"role": ROLE_EXPLORATION, "model": None},
}

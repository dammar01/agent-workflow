from config.roles import MODEL_CLAUDE, MODEL_KIMI

COMMAND_ROUTES = {
    "explore": (MODEL_KIMI,),
    "plan":    (MODEL_KIMI,),
    "analyze": (MODEL_KIMI,),
    "execute": (MODEL_CLAUDE,),
    "verify":  (MODEL_CLAUDE,),
}

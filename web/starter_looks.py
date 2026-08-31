"""Four immutable, current Scene v2 Composer built-ins."""
from copy import deepcopy
_DIGEST = "d0b8c0f9c7d55a8f58b6156e20c59afe6e4c5a7e2821cb6b3a29d9af81c296bf"
def _scene(seed, density, fold, glow, *, conway=False, clock=False):
    animation = {"component_id":"conway_life","version":1,"provider":"python","role":"animation","parameters":{"seed":seed,"rule":"B3/S23","initial_density":.14,"generations_per_second":5.0,"seed_cells":[]}} if conway else {"component_id":"aurora_curtains","version":1,"provider":"python","role":"animation","parameters":{"seed":seed,"curtain_density":density,"fold_depth":fold,"glow_intensity":glow,"source_fps":30}}
    widgets = [{"id":"clock","component":{"component_id":"clock_overlay","version":1,"provider":"python","role":"widget","parameters":{"format_24h":False,"show_seconds":True,"clock_offset_minutes":0,"color":[255,224,128]}},"visible":True,"placement":{"mode":"manual","strip_translation":0,"led_translation":-8}}] if clock else []
    return {"schema":"ledgrid.scene.v2","background":{"component_id":"native_aurora","version":1,"provider":"receiver_native","role":"background","bundle_digest":_DIGEST,"parameters":{"gain":glow,"source_fps":30,"seed":seed}},"animation":animation,"widgets":widgets,"plants":{"effects":{"version":1,"active":[],"strengths":{}}},"look":{"palette_id":"mist","pace":.7,"presentation_brightness":.82}}
_STARTERS = (("aurora","Aurora only",_scene(101,.34,.25,.45)),("aurora_clock","Aurora + Clock",_scene(202,.62,.72,.65,clock=True)),("aurora_conway","Aurora + Conway",_scene(303,.48,.42,.78,conway=True)),("aurora_conway_clock","Aurora + Conway + Clock",_scene(404,.7,.6,.8,conway=True,clock=True)))
def list_starters(): return [{"id":item[0],"name":item[1]} for item in _STARTERS]
def get_starter(starter_id):
    for identifier, name, scene in _STARTERS:
        if identifier == starter_id: return deepcopy({"id":identifier,"name":name,"scene":scene})
    raise ValueError("That starting point is unavailable.")

"""Explicit per-project framing choice; never waive speech or evidence checks."""
from . import engine, media_store
from .locking import project_write


def mode(rule):
    rule = rule or {}
    choice = rule.get('mode', 'full_body')
    if choice not in ('full_body', 'garment_display'):
        raise ValueError('Unknown hook framing')
    if choice == 'garment_display' and not str(rule.get('user_instruction', '')).strip():
        raise ValueError('Garment display framing requires the explicit user instruction')
    return choice


def fact(rule):
    return 'garment_display_visible' if mode(rule) == 'garment_display' else 'full_body_visible'


def keys(project_id, role):
    from . import commerce
    return tuple(fact(commerce.load(project_id).get('hook_framing')) if k == 'full_body_visible' else k
                 for k in commerce.FACTS[role])


@project_write
def configure(project_id, framing, user_instruction):
    from . import commerce
    instruction = media_store.text(user_instruction, 'explicit user framing instruction', 2000)
    rule = {'mode': framing, 'user_instruction': instruction}
    mode(rule)
    state = commerce.load(project_id)
    if state.get('selection') or any(r.get('role') == 'hook_picture' for r in state['reviews'].values()):
        raise ValueError('Framing is locked after a hook review or timeline selection; cannot relabel rejected evidence')
    state['hook_framing'] = rule
    commerce.save(project_id, state)
    return {'project_id': project_id, 'hook_framing': rule, 'required_hook_facts': list(keys(project_id, 'hook_picture')),
            'scope': 'Explicit user composition choice only. Same person/garment, natural non-speaking display, caption compatibility and actual review evidence remain required.'}

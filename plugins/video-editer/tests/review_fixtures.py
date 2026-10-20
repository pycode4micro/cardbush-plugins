"""Explicit synthetic perception receipts. Never use these as customer evidence."""
from video_editer import engine, delivery_review


def receipt(folder, media, modality='user_feedback', end=60):
    bound = delivery_review.fingerprint(media)
    path = folder / (bound['sha256'] + '-' + modality + '.json')
    engine.write_json(path, {'schema_version': 1, 'media': bound, 'modality': modality,
                            'ranges': [[0, end]], 'observation': 'Synthetic offline contract fixture',
                            'provenance': {'kind': 'user_feedback' if modality == 'user_feedback' else 'tool_result',
                                           'reference': 'synthetic-test-only', 'producer': 'offline-test-fixture'}})
    return str(path)

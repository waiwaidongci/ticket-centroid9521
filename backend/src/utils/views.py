from src.utils.time import iso


def dataset(item):
    return {'id': item.id, 'name': item.name, 'filename': item.filename, 'row_count': item.row_count,
            'checksum': item.checksum, 'author': item.author, 'created_at': iso(item.created_at)}


def run(item, detail=False):
    item_view = {'id': item.id, 'dataset_id': item.dataset_id, 'name': item.name, 'status': item.status,
                 'author': item.author, 'review': item.review, 'decision': item.decision,
                 'created_at': iso(item.created_at), 'finished_at': iso(item.finished_at),
                 'scheduled_at': iso(item.scheduled_at), 'schedule_id': item.schedule_id,
                 'has_export': bool(item.export_file), 'error': item.error}
    if detail:
        item_view.update(params=item.params, result=item.result, review_note=item.review_note)
    return item_view


def schedule(item):
    from src.domain.spec import SPEC
    return {'id': item.id, 'name': item.name, 'dataset_id': item.dataset_id, 'recipient': item.recipient,
            'next_at': iso(item.next_at), 'repeat_daily': item.repeat_daily, 'enabled': item.enabled,
            'next_report_day': item.params.get(SPEC['schedule_date_param']) if item.enabled else None}


def delivery(item):
    return {'id': item.id, 'run_id': item.run_id, 'recipient': item.recipient,
            'state': item.state, 'detail': item.detail, 'created_at': iso(item.created_at)}


def permutation(item, detail=False):
    total = item.total_combinations or 0
    settled = item.settled_combinations or 0
    view = {
        'id': item.id, 'dataset_id': item.dataset_id, 'name': item.name, 'status': item.status,
        'author': item.author, 'params': item.params, 'error': item.error,
        'total_combinations': total, 'settled_combinations': settled,
        'hit_combinations': item.hit_combinations,
        'progress': round(settled / total, 6) if total else 0.0,
        'created_at': iso(item.created_at), 'finished_at': iso(item.finished_at),
    }
    if detail:
        view.update(result=item.result,
                    sessions=item.snapshot.get('sessions', []),
                    tickets=item.snapshot.get('tickets', []),
                    observed_delta=item.snapshot.get('observed_delta'))
    return view


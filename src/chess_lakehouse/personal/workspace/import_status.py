"""User-facing outcomes, separate from diagnostic counters."""


def describe(job):
    new=job['new_games'];changed=job['changed_games'];pending=job.get('pending_games',0)
    if new:message=f'Added {new:,} new '+('game.' if new==1 else 'games.')
    elif changed:message=f'Refreshed {changed:,} '+('game.' if changed==1 else 'games.')
    elif pending:message='No newly finished games yet.'
    elif job['mode']=='replace':message='Your selected collection is ready.'
    else:message='Your library is up to date. No new completed games were found.'
    if new and changed:message+=f' Refreshed {changed:,} existing games.'
    if pending:message+=f' {pending:,} '+('game is' if pending==1 else 'games are')+' still in progress; sync again after '+('it finishes.' if pending==1 else 'they finish.')
    unfinished=job.get('skip_counts',{}).get('unfinished',0)
    if unfinished and not pending:
        message+=f' {unfinished:,} unfinished '+('game was' if unfinished==1 else 'games were')+' left out. '+('Upload the completed PGN again when ready.' if job.get('source_kind')=='file' else 'Sync again after completion.')
    labels={'empty':'empty or aborted','variant':'unsupported-variant','unrelated':'unrelated','unfinished':'unfinished'}
    details=[]
    for kind,count in job.get('skip_counts',{}).items():
        if not count or kind=='unfinished' and pending:continue
        details.append(f'{count:,} {labels.get(kind,kind)} '+('game was' if count==1 else 'games were')+' not imported.')
    return {'message':message,'details':details}

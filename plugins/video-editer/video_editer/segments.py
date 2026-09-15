"""Host-authored observations and clip candidates, pinned to source evidence."""
from . import media_ops, media_store as store
from pathlib import Path


def _reference(ref):
    if not isinstance(ref,dict) or set(ref)!={'id','version'} or not isinstance(ref['id'],str):
        raise ValueError('Reference requires id and pinned version')
    if not isinstance(ref['version'],int) or isinstance(ref['version'],bool) or ref['version']<1:
        raise ValueError('Reference version must be a positive integer')


def _quotes(value,start,end):
    if not isinstance(value,list) or len(value)>80: raise ValueError('quotes must be a list, max 80')
    result=[]
    for q in value:
        if not isinstance(q,dict) or set(q)!={'text','source_start','source_end'}: raise ValueError('Quote requires text/source_start/source_end')
        lo,hi=store.bounds(q['source_start'],q['source_end'],end)
        if lo<start: raise ValueError('Quote exceeds observation range')
        result.append({'text':store.text(q['text'],'quote',2000),'source_start':lo,'source_end':hi})
    return result


def annotate(project_id,asset_id,annotation,record_id=None,expected_version=0):
    a=media_ops.asset(project_id,asset_id)
    required={'source_start','source_end','summary','observation','evidence_refs','tags','quotes','uncertainty'}
    optional={'claims','risks','parent_ref','sampling_note'}
    if not isinstance(annotation,dict) or not required<=annotation.keys() or set(annotation)-required-optional:
        raise ValueError('Annotation has missing/unknown fields: '+','.join(sorted(required)))
    start,end=store.bounds(annotation['source_start'],annotation['source_end'],a['probe']['duration'])
    mode=annotation['observation']
    if mode not in ('audiovisual','audio','visual_frames','silent_video'): raise ValueError('Unknown observation modality')
    refs=annotation['evidence_refs']
    if not isinstance(refs,list) or not 1<=len(refs)<=40: raise ValueError('1-40 evidence_refs required')
    evidence=[]
    for ref in refs:
        _reference(ref)
        e=store.get(project_id,'evidence',ref['id'],ref['version'])
        if e['asset_id']!=asset_id or e['source_sha256']!=a['identity']['sha256']: raise ValueError('Evidence belongs to a different source')
        if any(media_ops.digest(Path(f['path']))[0]!=f['sha256'] for f in e['files']):
            raise ValueError('Evidence file changed; generate new evidence before annotation')
        evidence.append(e)
    if mode=='visual_frames':
        if any(e['operation']!='frames' for e in evidence): raise ValueError('Frame observation requires frame evidence')
        samples=[f['actual_source_time'] for e in evidence for f in e['frames'] if start<=f['actual_source_time']<end]
        if not samples: raise ValueError('No evidence frame falls within this annotation')
        if annotation['quotes']: raise ValueError('Frame-only observation cannot assert spoken quotes')
        observation_details={'sampled_source_times':samples,'interval_reviewed':False,
                             'sampling_note':store.text(annotation.get('sampling_note'),'sampling_note',1000)}
    else:
        if any(e['operation']!='preview' for e in evidence): raise ValueError('Continuous observation requires preview evidence')
        if mode in ('audio','audiovisual') and any(not e['probe']['has_audio'] for e in evidence):
            raise ValueError('Audio observation requires actual audio in the referenced preview')
        covered=store.merge([(e['source_start'],e['source_end']) for e in evidence])
        if not any(lo<=start and hi>=end for lo,hi in covered): raise ValueError('Preview evidence does not cover the claimed review range')
        if mode=='silent_video' and annotation['quotes']: raise ValueError('Silent visual review cannot assert speech')
        observation_details={'interval_reviewed':True,'reviewed_source_range':[start,end]}
    parent=annotation.get('parent_ref')
    if parent:
        _reference(parent)
        p=store.get(project_id,'segment',parent['id'],parent['version'])
        if p['asset_id']!=asset_id or p['source_sha256']!=a['identity']['sha256'] or not p['source_start']<=start<end<=p['source_end']:
            raise ValueError('Child segment must lie within its parent source/version')
        if parent['id']==record_id: raise ValueError('Segment cannot parent itself')
    result={**annotation,'source_start':start,'source_end':end,'summary':store.text(annotation['summary'],'summary'),
            'tags':store.strings(annotation['tags'],'tags'),'quotes':_quotes(annotation['quotes'],start,end),
            'uncertainty':store.text(annotation['uncertainty'],'uncertainty',2000),
            'claims':store.strings(annotation.get('claims',[]),'claims'),'risks':store.strings(annotation.get('risks',[]),'risks'),
            'observation_details':observation_details,'provenance':'host_agent_assertion; plugin validates evidence coverage, not semantic truth',
            'time_origin':a['probe']['time_origin'],'source_container_start':a['probe']['container_start']}
    return store.add(project_id,'segment',a,result,record_id,expected_version)


def candidate(project_id,asset_id,data,record_id=None,expected_version=0):
    a=media_ops.asset(project_id,asset_id)
    required={'source_start','source_end','segment_refs','reason','visual_evidence','boundary_review','tags','quotes','risks'}
    optional={'context','preferred_speed'}
    if not isinstance(data,dict) or not required<=data.keys() or set(data)-required-optional: raise ValueError('Candidate has missing/unknown fields')
    start,end=store.bounds(data['source_start'],data['source_end'],a['probe']['duration'])
    refs=data['segment_refs']
    if not isinstance(refs,list) or not 1<=len(refs)<=20: raise ValueError('1-20 segment_refs required')
    observations=[]
    for ref in refs:
        _reference(ref)
        p=store.get(project_id,'segment',ref['id'],ref['version'])
        if p['asset_id']!=asset_id or p['source_sha256']!=a['identity']['sha256']: raise ValueError('Candidate references a different source')
        observations.append(p)
    coverage=store.merge([(p['source_start'],p['source_end']) for p in observations])
    if not any(lo<=start<end<=hi for lo,hi in coverage): raise ValueError('Candidate exceeds indexed range')
    review=data['boundary_review']
    if not isinstance(review,dict) or set(review)!={'opening','ending','speech','action','note'}: raise ValueError('boundary_review requires opening/ending/speech/action/note')
    for key in ('opening','ending','speech','action'):
        if review[key] not in ('complete','incomplete','unknown','not_applicable'): raise ValueError('Invalid completeness assessment')
    review={**review,'note':store.text(review['note'],'boundary review note',2000)}
    quotes=_quotes(data['quotes'],start,end)
    if quotes:
        speech_ranges=store.merge([(p['source_start'],p['source_end']) for p in observations if p['observation'] in ('audio','audiovisual')])
        if not any(lo<=start<end<=hi for lo,hi in speech_ranges): raise ValueError('Speech candidate needs continuous audio-reviewed evidence')
        known=[q for p in observations for q in p['quotes']]
        if any(not any(q==k for k in known) for q in quotes): raise ValueError('Candidate quotes must reference exact indexed quotes/times; update the segment after review')
    elif review['speech']=='complete':
        raise ValueError('Complete speech requires an indexed quote; visual-only candidates may use not_applicable')
    speed=data.get('preferred_speed',1.0)
    if isinstance(speed,bool) or speed not in (1.0,1.25): raise ValueError('preferred_speed must be 1.0 or 1.25')
    result={**data,'source_start':start,'source_end':end,'reason':store.text(data['reason'],'reason'),
            'visual_evidence':store.text(data['visual_evidence'],'visual_evidence'),
            'context':store.text(data.get('context','No extra context supplied'),'context'),
            'tags':store.strings(data['tags'],'tags'),'risks':store.strings(data['risks'],'risks'),
            'quotes':quotes,'boundary_review':review,'preferred_speed':speed,'output_duration_hint':(end-start)/speed,
            'provenance':'host_agent_assessment; not automatic semantic approval','inserted_into_timeline':False}
    return store.add(project_id,'candidate',a,result,record_id,expected_version)


def coverage(project_id,asset_id,offset=0,limit=50):
    a=media_ops.asset(project_id,asset_id); duration=a['probe']['duration']
    store.number(offset,'offset',0,1000000); store.number(limit,'limit',1,200)
    if not isinstance(offset,int) or not isinstance(limit,int): raise ValueError('Pagination must be integers')
    groups={k:[] for k in ('technical_scan','preview_ready','agent_audiovisual','agent_audio','agent_silent_video')}
    samples=[]
    for e in store.rows(project_id,'evidence',asset_id):
        if e['source_sha256']!=a['identity']['sha256']: continue
        key={'scan':'technical_scan','preview':'preview_ready'}.get(e['operation'])
        if key: groups[key].append([e['source_start'],e['source_end']])
    for s in store.rows(project_id,'segment',asset_id):
        if s['source_sha256']!=a['identity']['sha256']: continue
        mode=s['observation']
        if mode=='visual_frames': samples.extend(s['observation_details']['sampled_source_times'])
        else: groups['agent_'+mode].append([s['source_start'],s['source_end']])
    output={}
    for name,intervals in groups.items():
        merged=store.merge(intervals); missing=store.gaps(merged,duration)
        output[name]={'seconds':sum(hi-lo for lo,hi in merged),'interval_count':len(merged),'intervals':merged[offset:offset+limit],
                      'uncovered_count':len(missing),'uncovered':missing[offset:offset+limit],
                      'next_offset':offset+limit if max(len(merged),len(missing))>offset+limit else None}
    samples=sorted(set(samples))
    return {'asset_id':asset_id,'source_sha256':a['identity']['sha256'],'duration':duration,'coverage':output,
            'sampled_frames':{'count':len(samples),'source_times':samples[offset:offset+limit],'interval_coverage':None},
            'note':'Preview readiness/technical scan is not Agent review. Sparse frames have no continuous understanding percentage. Review is host-reported.'}

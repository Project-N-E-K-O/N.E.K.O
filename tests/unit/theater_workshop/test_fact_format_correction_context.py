"""A factual format correction receives complete input and precise constraints, never clipped JSON."""
from copy import deepcopy
import json
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor
from .test_numeric_v2_generation import _quality_story_context,_generation_setup,_quality_reply,_quality_payload,_fact_review_payload


def test_fact_correction_omits_clipped_assistant_and_locates_the_bad_subject():
    story,authoring=_quality_story_context()
    fallback=_quality_reply(_quality_payload())
    corrected=[]
    def reply(messages,**options):
        request=json.loads(messages[1]['content']);op=options['operation']
        if op=='numeric_v2_fact_review' and request['audit_task']['id']=='node:mainline_01':
            payload=_fact_review_payload(request)
            check=next(c for c in payload['contract_checks'] if 'ownership_checks' in c)
            row=check['ownership_checks'][0]
            row.update(subject='原句没有的主体',owner='catgirl',kind='new_action')
            return json.dumps(payload,ensure_ascii=False)
        if op=='numeric_v2_fact_review_format_repair':
            assert [m['role'] for m in messages]==['system','user','user']
            detail=json.loads(messages[-1]['content'])
            assert 'target_id=mainline_01:entry:opening' in detail['validation_error']
            assert 'unit_id=u0' in detail['validation_error'] and 'field=subject' in detail['validation_error']
            assert detail['required_target_ids']==[t['id'] for t in request['contract_targets']]
            assert detail['required_issues']==[]
            assert '省略' in detail['instruction']
            corrected.append(detail)
            return json.dumps(_fact_review_payload(request),ensure_ascii=False)
        return fallback(messages,**options)
    before=deepcopy(story)
    NumericV2QualityAssessor(reply).assess(story=story,setup=_generation_setup(),authoring=authoring)
    assert len(corrected)==1 and story==before

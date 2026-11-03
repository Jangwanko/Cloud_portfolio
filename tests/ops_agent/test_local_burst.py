import threading
import time
import pytest
from scripts.run_local_burst import burst, sampled_ids, validate_limits


def test_limit_cannot_be_unbounded_or_nonlocal():
    for values in [(301,20,32),(300,21,32),(300,20,33),(0,20,32)]:
        with pytest.raises(ValueError): validate_limits(*values)


def test_duplicate_receipts_cannot_be_hidden_by_sampling():
    with pytest.raises(ValueError): sampled_ids([{'status':202,'request_id':'r'}]*100)
    rows=[{'status':202,'request_id':str(i)} for i in range(6000)]
    ids=sampled_ids(rows)
    assert len(ids)==len(set(ids))==60 and ids[0]=='0' and ids[-1]=='5999'


def test_in_flight_and_time_are_bounded(tmp_path):
    active=[0,0]
    lock=threading.Lock()
    def send(i):
        with lock:
            active[0]+=1
            active[1]=max(active[1],active[0])
        time.sleep(.05)
        with lock: active[0]-=1
        return {'status':202,'request_id':str(i)}
    rows,result=burst(send,100,1,2,threading.Event(),tmp_path/'receipts.jsonl')
    assert 1 <= len(rows) <= 100 and active[1] <= 2
    assert result['elapsed_seconds'] < 2


def test_repeated_errors_stop_and_preserve_receipts(tmp_path):
    rows,result=burst(lambda i:{'status':503},100,1,2,threading.Event(),tmp_path/'receipts.jsonl')
    assert result['stop_reason']=='REQUEST_ERROR_LIMIT'
    assert 5 <= len(rows) <= 7
    assert len((tmp_path/'receipts.jsonl').read_text().splitlines())==len(rows)

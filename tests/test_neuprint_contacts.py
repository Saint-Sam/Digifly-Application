import json
import threading

import pytest

from digifly_app.core.neuprint import NeuPrintError, AcquisitionCancelled
from digifly_app.core.neuprint_contacts import COLUMNS, fetch_between


class Client:
    dataset = 'male-cns:v1.0'
    def __init__(self, rows=None, threshold=.5):
        self.rows = rows if rows is not None else [[10001,800146,123,456,1.25,2.,3.,4.,5.,6.,.9,.8]]
        self.threshold = threshold
        self.calls = []
    def _request(self, method, url, *, payload, cancel, max_bytes, on_bytes):
        self.calls.append(payload)
        meta = 'MATCH (m:Meta)' in payload['cypher']
        result = {'columns':['threshold'] if meta else list(COLUMNS),
                  'data':[[self.threshold]] if meta else self.rows}
        raw = json.dumps(result).encode()
        if len(raw) > max_bytes:
            raise NeuPrintError('Byte budget exceeded')
        on_bytes(len(raw))
        return raw


def test_actual_locations_native_ids_confidence_and_exact_dataset_are_retained():
    client = Client(); received=[]
    contacts, method = fetch_between(client, [10001], [800146], on_bytes=received.append)
    assert contacts[0].to_row() == tuple(client.rows[0])
    assert all(call['dataset']=='male-cns:v1.0' for call in client.calls)
    q=client.calls[-1]['cypher']
    assert 'WITH DISTINCT' in q and 'pre.confidence > 0.5' in q
    assert 'post.confidence > 0.5' in q and 'LIMIT 250001' in q
    assert method['electrical_anatomy_inferred'] is False
    assert method['coordinates_rounded'] is False and sum(received)>0


@pytest.mark.parametrize('mutation', ['duplicate','nan','bool_coordinate','float_identity','outside_body','low_confidence','bad_confidence'])
def test_invalid_site_rows_fail_without_returning_partial_locations(mutation):
    c=Client()
    if mutation=='duplicate': c.rows*=2
    if mutation=='nan': c.rows[0][5]=float('nan')
    if mutation=='bool_coordinate': c.rows[0][5]=True
    if mutation=='float_identity': c.rows[0][2]=123.5
    if mutation=='outside_body': c.rows[0][0]=999
    if mutation=='low_confidence': c.rows[0][11]=.5
    if mutation=='bad_confidence': c.rows[0][11]=1.1
    with pytest.raises(NeuPrintError,match='Invalid, duplicated'):
        fetch_between(c,[10001],[800146])


def test_cancellation_before_entry_performs_no_query():
    c=Client(); cancel=threading.Event();cancel.set()
    with pytest.raises(AcquisitionCancelled): fetch_between(c,[10001],[800146],cancel=cancel)
    assert c.calls==[]


def test_row_and_shared_byte_limits_refuse_partial_data():
    c=Client();c.rows.append([10001,800146,124,457,1.,2.,3.,4.,5.,6.,.9,.8])
    with pytest.raises(NeuPrintError,match='row limit'): fetch_between(c,[10001],[800146],max_rows=1)
    with pytest.raises(NeuPrintError,match='Byte budget'): fetch_between(Client(),[10001],[800146],max_bytes=100)


def test_absent_threshold_uses_recorded_official_default_not_a_guessed_cutoff():
    c=Client(rows=[],threshold=None)
    rows, method=fetch_between(c,[10001],[800146])
    assert rows==() and method['confidence_threshold']==0.
    assert '.confidence >' not in c.calls[-1]['cypher']


def test_confidence_cache_is_scoped_to_exact_dataset():
    c=Client(rows=[])
    fetch_between(c,[10001],[800146])
    c.dataset='manc:v1.2.1';c.threshold=.7
    _,method=fetch_between(c,[10000],[10068])
    assert method['confidence_threshold']==.7
    assert sum('MATCH (m:Meta)' in row['cypher'] for row in c.calls)==2


@pytest.mark.parametrize('limits', [{'max_rows':1.5},{'max_rows':True},{'max_bytes':False}])
def test_noninteger_limits_do_not_reach_server(limits):
    c=Client()
    with pytest.raises(ValueError,match='limits'): fetch_between(c,[10001],[800146],**limits)
    assert c.calls==[]

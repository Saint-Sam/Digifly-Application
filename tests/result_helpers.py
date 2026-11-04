"""Assertions over either completed recording format, without rewriting a run."""
import csv
from digifly_app.workers.result_storage import iter_binary_traces,open_result_text,result_path


def voltage_rows(root):
    path=result_path(root/'voltage_traces.csv')
    if path.name!='ready.json':
        with open_result_text(path) as stream:
            return list(csv.DictReader(stream))
    return [dict(condition=key[0],repetition=str(key[1]),neuron_id=key[2],compartment_id=key[3],
                 time_ms=str(t),voltage_mV=str(v))
            for key,times,values in iter_binary_traces(path.parent) for t,v in zip(times,values)]

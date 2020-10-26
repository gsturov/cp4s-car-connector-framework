import requests, json, urllib
from enum import Enum
from car_framework.util import get_json, ImportJobStatus, recoverable_failure_status_code, RecoverableFailure, UnrecoverableFailure
from car_framework.context import context
import time

IMPORT_RESOURCE = '/imports'
STATUS_RESOURCE = '/importstatus'
DATABASE_RESOURCE = '/databases'
JOBSTATUS_RESOURCE = '/jobstatus'
SOURCE_RESOURCE = '/source'
GRAPH_SEARCH = '/graph'

FULL_IMPORT_IN_PROGRESS_ENDPOINT = '/full-import-in-progress'
MODEL_STATE_ID = 'model_state_id'
max_wait_time = 60


class CarDbStatus(Enum):
    FAILURE = 0
    READY = 1
    NEWLY_CREATED = 2


class CarService(object):

    def __init__(self, communicator):
        self.communicator = communicator
        self.car_url = context().args.car_service


    def get_model_state_id(self):
        url = '%s/source/%s' % (self.car_url, urllib.parse.quote_plus(context().args.source))
        resp = self.communicator.get(url)
        if resp.status_code != 200:
            return None
        json_data = resp.json()
        return json_data and json_data.get(MODEL_STATE_ID)


    def save_model_state_id(self, new_model_state_id):
        data = json.dumps({ MODEL_STATE_ID: new_model_state_id })
        resp = self.communicator.patch(self.car_url + SOURCE_RESOURCE, data=data, params={ 'key': context().args.source })
        if resp.status_code != 200:
            raise Exception('Error when trying to save a save point: %d' % resp.status_code)


    def reset_model_state_id(self):
        self.save_model_state_id('')


    def import_data(self, data):
        status = ImportJobStatus()
        try:
            json_data = json.dumps(data)
            resp = self.communicator.post(self.car_url + IMPORT_RESOURCE, data=json_data)
            status.status_code = resp.status_code
            json_resp = get_json(resp)
            if 'id' in json_resp:
                status.job_id = json_resp['id']
                status.status = ImportJobStatus.IN_PROGRESS
            else:
                status.status = ImportJobStatus.FAILURE
                status.error = str(json_resp)
            return status

        except Exception as e:
            status.status = ImportJobStatus.FAILURE
            status.error = str(e)
            return status


    def check_import_status(self, statuses):
        # for IN_PROGRESS statuses create a map: id -> status
        jobs_to_check = dict(map(lambda s: (s.job_id, s), filter(lambda s: s.status is ImportJobStatus.IN_PROGRESS, statuses)))
        
        wait_time = 1
        try:
            while True:
                if not jobs_to_check: return
                params = ','.join(jobs_to_check.keys())
                resp = self.communicator.get(self.car_url + STATUS_RESOURCE, params={'ids': params})
                data = get_json(resp)
                if 'error_imports' in data:
                    for err in data['error_imports']:
                        id = err['id']
                        jobs_to_check[id].status = ImportJobStatus.FAILURE
                        jobs_to_check[id].error = err.get('error')
                        jobs_to_check[id].status_code = err.get('statusCode')
                        jobs_to_check.pop(id, None)

                incomplete_ids = []
                if 'incomplete_imports' in data:
                    incomplete = data['incomplete_imports']
                    if incomplete:
                        context().logger.info('The following imports are still in progress:')
                        incomplete_ids = list(map(lambda item: item['id'], incomplete))
                        for id in incomplete_ids:
                            context().logger.info('id: %s' % id)

                done = filter(lambda id: id not in incomplete_ids, list(jobs_to_check.keys()))
                for id in done:
                    jobs_to_check[id].status = ImportJobStatus.SUCCESS
                    del jobs_to_check[id]

                if not jobs_to_check: return
                time.sleep(wait_time)
                if wait_time < max_wait_time: wait_time *= 2
                if wait_time > max_wait_time: wait_time = max_wait_time

        except Exception as e:
            # mark all remaining statuses as failed
            for s in jobs_to_check.values():
                s.status = ImportJobStatus.FAILURE
                s.error = str(e)
        

    def delete(self, resource, ids):
        # report and source not mentioned anywhere coz connectors aren't allowed to delete it
        key_based = ["ipaddress", "hostname", "macaddress"]
        external_id_based = ["asset", "container", "user", "account", "application", "database", "port", "vulnerability", "geolocation"]
        if resource in key_based:
            url = '%s/source/%s/%s?external_ids=%s' % (self.car_url, context().args.source, resource, ids)
        elif resource in external_id_based:
            url = '%s/source/%s/%s?external_ids=%s' % (self.car_url, context().args.source, resource, ids)
        r = self.communicator.delete(url)
        return r.status_code


    def get_db_status(self):
        db_url = self.car_url + DATABASE_RESOURCE
        r = self.communicator.get(db_url)
        status_code = r.status_code
        
        if status_code == 400:
            # the database is not setup yet, create it
            r = self.communicator.post(db_url)
            job_id = get_json(r)['job_id']
            status = self.wait_until_done(job_id)
            if status == CarDbStatus.READY:
                return CarDbStatus.NEWLY_CREATED
            else:
                return CarDbStatus.FAILURE
        
        elif status_code == 200:
            r_json = get_json(r)
            databases = r_json['databases']
            if databases[0]['is_ready'] == True:
                return CarDbStatus.READY
            elif databases[0]['graph_name'] == '':
                # create the graph
                payload = json.dumps({ 'graph_name': 'assets'})
                r = self.communicator.patch(db_url, data=payload)
                job_id = get_json(r)['job_id']
                status = self.wait_until_done(job_id)
                if status == CarDbStatus.READY:
                    return CarDbStatus.NEWLY_CREATED
                else:
                    return CarDbStatus.FAILURE
            elif len(databases[0]['collections_without_indexes']) > 0:
                payload = json.dumps({ 'collections_without_indexes': databases[0]['collections_without_indexes']})
                r = self.communicator.patch(db_url, data=payload)
                job_id = get_json(r)['job_id']
                status = self.wait_until_done(job_id)
                if status == CarDbStatus.READY:
                    return CarDbStatus.NEWLY_CREATED
                else:
                    return CarDbStatus.FAILURE
        
        elif recoverable_failure_status_code(status_code):
            raise RecoverableFailure('Getting the following status code when accessing ISC CAR service: %d' % status_code)
        else:
            raise UnrecoverableFailure('Getting the following status code when accessing ISC CAR service: %d' % status_code)


    def graph_search(self, resource, search_id, source=""):
        external_id = urllib.parse.quote_plus(search_id)

        url = '%s/%s/%s%s' % (self.car_url, resource, external_id, GRAPH_SEARCH)
        r = {}
        if source == "":
            r = self.communicator.get(url)
        else:
            r = self.communicator.get(url, params={'source': source})

        if r.status_code == 200:
            return get_json(r)
        elif recoverable_failure_status_code(r.status_code):
            raise RecoverableFailure('Error occurred while searching collection: %d' % r.status_code)
        else:
            raise UnrecoverableFailure('Error occurred while searching collection: %d' % r.status_code)
            

    def graph_attribute_search(self, resource, attribute, search_id):
        external_id = urllib.parse.quote_plus(search_id)
        url = '%s/%s?%s=%s' % (self.car_url, resource, attribute, external_id)
        r = self.communicator.get(url)
        if r.status_code == 200:
            return get_json(r)
        elif recoverable_failure_status_code(r.status_code):
            raise RecoverableFailure('Error occurred while searching collection attribute: %d' % r.status_code)
        else:
            raise UnrecoverableFailure('Error occurred while searching collection attribute: %d' % r.status_code)


    def database_patch_value(self, tags):
        data = dict()
        if 'name' in tags:
            data.update({'name': tags['name']})
        elif 'pending_update' in tags:
            data.update({'pending_update': tags['pending_update']})

        query_expression = json.dumps(data).encode("utf-8")
        resource_type = "/{resource}".format(resource=tags['resource_type'])
        param = {
            'external_id': tags['resource_id'],
        }

        r = self.communicator.patch(self.car_url + resource_type, data=query_expression,
                                                    params=param)

        if r.status_code == 200:
            return get_json(r)
        elif recoverable_failure_status_code(r.status_code):
            raise RecoverableFailure('Error occurred while searching collection attribute: %d' % r.status_code)
        else:
            raise UnrecoverableFailure('Error occurred while searching collection attribute: %d' % r.status_code)
 
    
    def edge_patch(self, source, edge_id, data):
        query_expression = json.dumps(data).encode("utf-8")
        resource_type = "/{resource}".format(resource=edge_id['edge_type'])
        param = {
            'source': source,
            'from': edge_id['from'],
            'to': edge_id['to']
        }
        r = self.communicator.patch(self.car_url + resource_type, data=query_expression, params=param)

        if r.status_code == 200:
            return get_json(r)
        elif recoverable_failure_status_code(r.status_code):
            raise RecoverableFailure('Error occurred while updating edge: %d' % r.status_code)
        else:
            raise UnrecoverableFailure('Error occurred while updating edge: %d' % r.status_code)
            

    def wait_until_done(self, job_id):
        while True:
            r = self.communicator.get(self.car_url + JOBSTATUS_RESOURCE + '/{}'.format(job_id))
            if r.status_code == 200:
                status = get_json(r)['status']
                if status == 'COMPLETE':
                    return CarDbStatus.READY
                if status == 'ERROR':
                    return CarDbStatus.FAILURE
            else:
                return CarDbStatus.FAILURE


    def enter_full_import_in_progress_state(self):
        endpoint = '%s/source/%s%s' % (self.car_url, context().args.source, FULL_IMPORT_IN_PROGRESS_ENDPOINT)
        r = self.communicator.post(endpoint)
        job_id = get_json(r)['job_id']
        self.wait_until_done(job_id)
        return r.status_code


    def exit_full_import_in_progress_state(self):
        endpoint = '%s/source/%s%s' % (self.car_url, context().args.source, FULL_IMPORT_IN_PROGRESS_ENDPOINT)
        r = self.communicator.delete(endpoint)
        job_id = get_json(r)['job_id']
        self.wait_until_done(job_id)
        return r.status_code


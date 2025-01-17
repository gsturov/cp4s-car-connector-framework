from enum import Enum
import json
from math import floor

BATCH_SIZE = 20
deprecate_msg_printed = {}


def deprecate(func):
    fn_name = '%s.%s' % (func.__module__, func.__name__)
    def inner(*args, **kwargs):
        global depricate_msg_printed
        if not deprecate_msg_printed.get(fn_name):
            from car_framework.context import context
            context().logger.warning('%s function is deprecated and will be removed in upcoming versions. Consider rewriting the request without using %s' % (fn_name, func.__name__))
            deprecate_msg_printed[fn_name] = True
        return func(*args, **kwargs)
    return inner


def recoverable_failure_status_code(status_code):
    return status_code in (302, 400, 401, 403, 408, 500, 503, 504)


def check_status_code(status_code, operation):
    if floor(status_code / 100) != 2:
        message = 'Failure detected. Operation: %s, Status code: %s' % (operation, status_code)
        if recoverable_failure_status_code(status_code): raise RecoverableFailure(message)
        else: raise UnrecoverableFailure(message)


def get_json(response):
    try: return response.json()
    except: return {}


def get(var, path):
    fields = path.split('.')
    v = var
    for f in fields:
        v = v.get(f)
        if v == None: return None
    return v

class ErrorCode(Enum):
    # https://komodor.com/learn/exit-codes-in-containers-and-kubernetes-the-complete-guide/
    ## kubectl preserved
    # Exit Code 0
    # Exit Code 1: Application Error
    # Exit Code 125
    # Exit Code 126: Command Invoke Error
    # Exit Code 127: File or Directory Not Found
    # Exit Code 128: Invalid Argument Used on Exit
    # Exit Code 134: Abnormal Termination (SIGABRT)
    # Exit Code 137: Immediate Termination (SIGKILL)
    # Exit Code 139: Segmentation Fault (SIGSEGV)
    # Exit Code 143: Graceful Termination (SIGTERM)
    # Exit Code 255: Exit Status Out Of Range

    # If the Exit Code is 0 – the container exited normally, no troubleshooting is required
    # If the Exit Code is between 1-128 – the container terminated due to an internal error, such as a missing or invalid command in the image specification
    # If the Exit Code is between 129-255 – the container was stopped as the result of an operating signal, such as SIGKILL or SIGINT
    # If the Exit Code was exit(-1) or another value outside the 0-255 range, kubectl translates it to a value within the 0-255 range.

    GENERAL_APPLICATION_FAILURE = 1 # Unknown
    CONNECTOR_RUNTIME_INVALID_PARAMETER = 2 # python command argumens problem 
    RECOVERABLE_DEFAULT_FAILURE = 10 # RecoverableFailure exception default code
    # RECOVERABLE_DATABASE_FAILURE = 11 # Database is not ready
    # RECOVERABLE_UPDATE_COLLECTION_FAILURE = 12 # Error occurred while pathcing collection
    # RECOVERABLE_UPDATE_EDGE_FAILURE = 13 # Error occurred while updating edge
    # RECOVERABLE_IMPORT_JOB_FAILURE = 14 # Import job failure
    UNRECOVERABLE_FAILURE_DEFAULT = 20 # UnrecoverableFailure exception default code
    DATASOURCE_FAILURE_DEFAULT = 50 # Unknown
    DATASOURCE_FAILURE_CONNECT = 51 # Service unavailable
    DATASOURCE_FAILURE_AUTH = 52 # Authentication fail
    DATASOURCE_FAILURE_FORBIDDEN = 53 # Forbidden
    DATASOURCE_FAILURE_INVALID_PARAMETER = 54 # Invalid parameter
    DATASOURCE_FAILURE_DATA_PROCESS = 55 # Error while processing received data

class BaseConnectorFailure(Exception):
    def __init__(self, message, code: int):
        from car_framework.context import context
        context().logger.error(message)
        self.message = message
        self.code = code

class RecoverableFailure(BaseConnectorFailure):
    def __init__(self, message, code=ErrorCode.RECOVERABLE_DEFAULT_FAILURE.value):
        super().__init__(message, code)

class UnrecoverableFailure(BaseConnectorFailure):
    def __init__(self, message, code=ErrorCode.UNRECOVERABLE_FAILURE_DEFAULT.value):
        super().__init__(message, code)

class IncrementalImportNotPossible(Exception):
    callback = None
    def __init__(self, message):
        from car_framework.context import context
        context().logger.info(message)
        self.message = message

class DatasourceFailure(BaseConnectorFailure):
    def __init__(self, message, code=ErrorCode.DATASOURCE_FAILURE_DEFAULT.value):
        super().__init__(message, code)


def check_for_error(status):
    if status.get('errors') and len(status['errors']) > 0:
        raise UnrecoverableFailure('Import job failure. Errors: ' + json.dumps(status['errors']))


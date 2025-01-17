import argparse, traceback, sys, os

from car_framework.context import Context, context
from car_framework.util import ErrorCode, IncrementalImportNotPossible, RecoverableFailure, UnrecoverableFailure, DatasourceFailure


class BaseApp(object):
    def __init__(self, description):
        self.parser = argparse.ArgumentParser(description=description)
        self.parser.add_argument('-car-service-url', dest='car_service_apikey_url', default=os.getenv('CAR_SERVICE_URL',None), type=str, required=False, help='URL of the CAR ingestion service if API key is used for authorization')
        self.parser.add_argument('-car-service-key', dest='api_key', default=os.getenv('CAR_SERVICE_KEY',None), type=str, required=False, help='API key for CAR ingestion service')
        self.parser.add_argument('-car-service-password', dest='api_password', default=os.getenv('CAR_SERVICE_PASSWORD',None), type=str, required=False, help='Password for CAR ingestion service')

        self.parser.add_argument('-car-service-url-for-token', dest='car_service_token_url', default=os.getenv('CAR_SERVICE_URL_FOR_AUTHTOKEN',None), type=str, required=False, help='URL of the CAR ingestion service if Auth token is used for authorization')
        self.parser.add_argument('-car-service-token', dest='api_token', default=os.getenv('CAR_SERVICE_AUTHTOKEN',None), type=str, required=False, help='Auth token for CAR ingestion service')

        # source id to uniquely identify each data source
        self.parser.add_argument('-source', dest='source', default=os.getenv('CONNECTION_NAME',None), type=str, required=False, help='Unique source id for the data source')
        self.parser.add_argument('-name', dest='connector_name', default=os.getenv('CONNECTOR_NAME', None), type=str, required=False, help='Name of the connector')
        self.parser.add_argument('-version', dest='version', default=os.getenv('CONNECTOR_VERSION', None), type=str, required=False, help='Connector version number')

        self.parser.add_argument('-d', dest='debug', action='store_true', default=os.getenv('DEBUG', False), help='Enables DEBUG level logging')
        self.parser.add_argument('-connection-test', dest='connection_test', type=bool, default=os.getenv('DATASOURCE_CONNECTION_TEST', False), help='Only perform datasource connection test and exit, if this parameter is present with any value.')
        self.parser.add_argument('-export-data-dir', dest='export_data_dir', default='/tmp/car_temp_export_data', help='Export data directory path, deafualt /tmp/car_temp_export_data')
        self.parser.add_argument('-keep-export-data-dir', dest='keep_export_data_dir', action='store_true', help='True for not removing export_data directory after complete, default false')
        self.parser.add_argument('-export-data-page-size', dest='export_data_page_size', type=int, default=2000, help='File export_data dump page size, default 2000')


    def setup(self):
        args = self.parser.parse_args()
        self.args = args

        if not args.api_token:
            if not args.api_key or not args.api_password:
                self.parser.print_usage(sys.stderr)
                sys.stderr.write('Either -car-service-token or -car-service-key and -car-service-password arguments are required.')
                sys.exit(ErrorCode.CONNECTOR_RUNTIME_INVALID_PARAMETER.value)

        if not args.car_service_apikey_url and not args.car_service_token_url:
            self.parser.print_usage(sys.stderr)
            sys.stderr.write('Either -car-service-url or -car-service-url-for-token is required.')
            sys.exit(ErrorCode.CONNECTOR_RUNTIME_INVALID_PARAMETER.value)

        if args.car_service_apikey_url:
            if not args.api_key or not args.api_password:
                self.parser.print_usage(sys.stderr)
                sys.stderr.write('If -car-service-url is provided then -car-service-key and -car-service-password arguments are required.')
                sys.exit(ErrorCode.CONNECTOR_RUNTIME_INVALID_PARAMETER.value)

        if args.car_service_token_url:
            if not args.api_token:
                self.parser.print_usage(sys.stderr)
                sys.stderr.write('If -car-service-url-for-token is provided then -car-service-token argument is required.')
                sys.exit(ErrorCode.CONNECTOR_RUNTIME_INVALID_PARAMETER.value)

        if not args.source:
            self.parser.print_usage(sys.stderr)
            sys.stderr.write('Missing required -source argument.')
            sys.exit(ErrorCode.CONNECTOR_RUNTIME_INVALID_PARAMETER.value)

        Context(args)


    def run(self):
        try:
            if self.args.connection_test:
                if hasattr(context(), 'asset_server') and hasattr(context().asset_server, 'test_connection') :
                    context().logger.info('Testing the datasource connection ... ')
                    code = context().asset_server.test_connection()
                    if code == 0:
                        context().logger.info('Testing the datasource connection was successful.')
                    else:
                        context().logger.error('Testing the datasource connection failed with code ' + str(code))
                    sys.exit(code)
                else:
                    raise DatasourceFailure("The connector did not implement connection_test call.")
            else:
                try:
                    extension = self.get_schema_extension()
                    if extension: extension.setup()

                    context().logger.info('Attempting incremental import...')
                    context().inc_importer.run()
                except IncrementalImportNotPossible as e:
                    context().logger.info('Attempting full import...')
                    context().full_importer.run()

            context().logger.info('Done.')

        except RecoverableFailure as e:
            context().logger.info('Recoverable failure: ' + e.message)
            context().logger.info('Incremental import will be attempted again in the next run.')
            sys.exit(e.code)
        except UnrecoverableFailure as e:
            context().logger.info('Unrecoverable failure: ' + e.message)
            context().logger.info('Incremental import will not be possible in the next run.')
            context().car_service.reset_model_state_id()
            sys.exit(e.code)
        except DatasourceFailure as e:
            context().logger.info('Datasource failure: ' + str(e.message))
            sys.exit(e.code)
        except Exception as e:
            context().logger.exception(e)
            context().logger.error(traceback.format_exc())
            # traceback.print_exc()
            sys.exit(ErrorCode.GENERAL_APPLICATION_FAILURE.value)


    def get_schema_extension(self):
        return None

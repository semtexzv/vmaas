#!/usr/bin/env python3
"""
Main web API module
"""

import os
import signal
import sre_constants
import json
import yaml
import asyncio

from jsonschema.exceptions import ValidationError
from prometheus_client import generate_latest
from aiohttp import web, ClientSession, WSMessage, WSMsgType, hdrs
import connexion

from cache import Cache
from cve import CveAPI
from repos import RepoAPI
from updates import UpdatesAPI
from errata import ErrataAPI
from packages import PackagesAPI
from vulnerabilities import VulnerabilitiesAPI
from dbchange import DBChange
from common.logging_utils import init_logging, get_logger
from probes import REQUEST_COUNTS, REQUEST_TIME

# pylint: disable=too-many-lines

VMAAS_VERSION = os.getenv("VMAAS_VERSION", "unknown")
PUBLIC_API_PORT = 8080
MAX_SERVERS = "1"

WEBSOCKET_RECONNECT_INTERVAL = 60
LOGGER = get_logger(__name__)


class BaseHandler:
    """Base class containing individual repositories"""

    db_cache = None
    updates_api = None
    repo_api = None
    cve_api = None
    errata_api = None
    packages_api = None
    vulnerabilities_api = None
    dbchange_api = None

    def data_received(self, chunk):
        pass

    def options(self):
        """Answer OPTIONS request."""

    @classmethod
    async def get_post_data(cls, request):
        """extract input JSON from POST request"""
        data = None

        if request.headers[hdrs.CONTENT_TYPE] == 'application/json':
            return await request.json()
        else:
            raise ValueError("Only application/json supported for now")

    @classmethod
    async def handle_request(cls, api_endpoint, api_version,  param_name=None, param=None, request = None,**kwargs):
        """Takes care of validation of input and execution of request."""

        data = None
        try:
            if request.method == 'POST':
                data = await cls.get_post_data(request)
            else:
                data = {param_name: [param]}
            res = api_endpoint.process_list(api_version, data)
            code = 200
        except ValidationError as valid_err:
            if valid_err.absolute_path:
                res = '%s : %s' % (valid_err.absolute_path.pop(), valid_err.message)
            else:
                res = '%s' % valid_err.message
            code = 400
        except (ValueError, sre_constants.error) as ex:
            res = repr(ex)
            code = 400
        except Exception as err:  # pylint: disable=broad-except
            err_id = err.__hash__()
            res = 'Internal server error <%s>: please include this error id in bug report.' % err_id
            code = 500
            LOGGER.exception(res)
            LOGGER.error("Input data for <%s>: %s", err_id, data)
        return web.json_response(res, status=code)

    @classmethod
    def on_finish(cls, request):
        REQUEST_TIME.labels(request.method, request.path).observe(request.request_time())
        REQUEST_COUNTS.labels(request.method, request.path, request.stats_code ).inc()
        LOGGER.debug("request called - method: %s, status: %d, path: %s, request_time: %f", request.method,
                     request.stats_code, request.path, request.request_time())


class HealthHandler(BaseHandler):
    """Handler class providing health status."""

    @classmethod
    async def get(cls, **kwargs):
        """Get API status.
           ---
           description: Return API status
           responses:
             200:
               description: Application is alive
        """


class VersionHandler(BaseHandler):
    """Handler class providing app version."""

    @classmethod
    async def get(cls, **kwargs):
        """Get app version.
           ---
           description: Get version of application
           responses:
             200:
               description: Version of application returned
        """
        return VMAAS_VERSION, 200


class DBChangeHandler(BaseHandler):
    """
    Class to return last-updated information from VMaaS DB
    """

    @classmethod
    async def get(cls, **kwargs):
        """Get last-updated-times for VMaaS DB """
        return web.json_response(cls.dbchange_api.process())


class UpdatesHandlerGet(BaseHandler):
    """Handler for processing /updates GET requests."""

    @classmethod
    async def get(cls, nevra=None, **kwargs):
        """List security updates for single package NEVRA """
        return await cls.handle_request(cls.updates_api, 1, 'package_list', nevra, **kwargs)


class UpdatesHandlerPost(BaseHandler):
    """Handler for processing /updates POST requests."""

    @classmethod
    async def post(cls, **kwargs):
        """List security updates for list of package NEVRAs"""
        return await cls.handle_request(cls.updates_api, 1, **kwargs)


class UpdatesHandlerV2Get(BaseHandler):
    """Handler for processing /updates GET requests."""

    @classmethod
    async def get(cls, nevra=None, **kwargs):
        """List security updates for single package NEVRA """
        return await cls.handle_request(cls.updates_api, 2, 'package_list', nevra, **kwargs)


class UpdatesHandlerV2Post(BaseHandler):
    """Handler for processing /updates POST requests."""

    @classmethod
    async def post(cls, **kwargs):
        """List security updates for list of package NEVRAs"""
        return await cls.handle_request(cls.updates_api, 2, **kwargs)


class CVEHandlerGet(BaseHandler):
    """Handler for processing /cves GET requests."""

    @classmethod
    async def get(cls, cve=None, **kwargs):
        """
        Get details about CVEs. It is possible to use POSIX regular expression as a pattern for CVE names.
        """
        return await cls.handle_request(cls.cve_api, 1, 'cve_list', cve, **kwargs)


class CVEHandlerPost(BaseHandler):
    """Handler for processing /cves POST requests."""

    @classmethod
    async def post(cls, **kwargs):
        """
        Get details about CVEs with additional parameters. As a "cve_list" parameter a complete list of CVE
        names can be provided OR one POSIX regular expression.
        """
        return await cls.handle_request(cls.cve_api, 1, **kwargs)


class ReposHandlerGet(BaseHandler):
    """Handler for processing /repos GET requests."""

    @classmethod
    async def get(cls, repo=None, **kwargs):
        """
        Get details about a repository or repository-expression. It is allowed to use POSIX regular
        expression as a pattern for repository names.
        """
        return await cls.handle_request(cls.repo_api, 1, 'repository_list', repo, **kwargs)


class ReposHandlerPost(BaseHandler):
    """Handler for processing /repos POST requests."""

    @classmethod
    async def post(cls, **kwargs):
        """
        Get details about list of repositories. "repository_list" can be either a list of repository
        names, OR a single POSIX regular expression.
        """
        return await cls.handle_request(cls.repo_api, 1, **kwargs)


class ErrataHandlerGet(BaseHandler):
    """Handler for processing /errata GET requests."""

    @classmethod
    async def get(cls, erratum=None, **kwargs):
        """
        Get details about errata. It is possible to use POSIX regular
        expression as a pattern for errata names.
        """
        return await cls.handle_request(cls.errata_api, 1, 'errata_list', erratum, **kwargs)


class ErrataHandlerPost(BaseHandler):
    """ /errata API handler """

    @classmethod
    async def post(cls, **kwargs):
        """
        Get details about errata with additional parameters. "errata_list"
        parameter can be either a list of errata names OR a single POSIX regular expression.
        """
        return await cls.handle_request(cls.errata_api, 1, **kwargs)


class PackagesHandlerGet(BaseHandler):
    """Handler for processing /packages GET requests."""

    @classmethod
    async def get(cls, nevra=None, **kwargs):
        """Get details about packages."""
        return await cls.handle_request(cls.packages_api, 1, 'package_list', nevra, **kwargs)


class PackagesHandlerPost(BaseHandler):
    """ /packages API handler """

    @classmethod
    async def post(cls, **kwargs):
        """Get details about packages. "package_list" must be a list of"""
        return await cls.handle_request(cls.packages_api, 1, **kwargs)


class VulnerabilitiesHandlerGet(BaseHandler):
    """Handler for processing /vulnerabilities GET requests."""

    @classmethod
    async def get(cls, nevra=None, **kwargs):
        """ List of applicable CVEs for a single package NEVRA
        """
        return await cls.handle_request(cls.vulnerabilities_api, 1, 'package_list', nevra, **kwargs)


class VulnerabilitiesHandlerPost(BaseHandler):
    """Handler for processing /vulnerabilities POST requests."""

    @classmethod
    async def post(cls, **kwargs):
        """List of applicable CVEs to a package list. """
        return await cls.handle_request(cls.vulnerabilities_api, 1,**kwargs)


class Application:
    """ main webserver application class """
    def __init__(self):
        self.websocket_url = "ws://%s:8082/" % os.getenv("WEBSOCKET_HOST", "vmaas_websocket")
        self.websocket = None
        self.websocket_response_queue = set()

    def stop(self):
        """Stop vmaas application"""
        if self.websocket is not None:
            self.websocket.close()
            self.websocket = None
        # TODO: Which one here
        asyncio.current_task().cancel()
        #asyncio.get_event_loop().stop()

    @staticmethod
    def _refresh_cache():
        BaseHandler.db_cache.reload()
        use_hot_cache = os.getenv("HOTCACHE_ENABLED", "YES")
        if use_hot_cache.upper() == "YES":
            BaseHandler.updates_api.clear_hot_cache()
        LOGGER.info("Cached data refreshed.")

    async def websocket_loop(self):
        async with ClientSession() as session:
            while True:
                async with session.ws_connect(url=self.websocket_url) as ws:
                    LOGGER.info("Connected to: %s", self.websocket_url)
                    self.websocket = ws

                    await self.websocket.send_str("subscribe-webapp")

                    for item in self.websocket_response_queue:
                        await self.websocket.send_str(item)

                    self.websocket_response_queue.clear()

                    # handle websocket messages
                    await self.websocket_msg_handler()
                    self.websocket = None
                    # Reconnection sleep, then, the outer loop will begin again, reconnecting this client
                    await asyncio.sleep(WEBSOCKET_RECONNECT_INTERVAL * 1000)


    async def websocket_msg_handler(self):
        async for msg in self.websocket:

            LOGGER.info(f"Websocket message: f{msg.type}, f{msg.data}")
            if msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                return None
            elif msg.data == 'refresh-cache':
                self._refresh_cache()
                msg = f"refreshed {BaseHandler.db_cache.dbchange['exported']}"
                if self.websocket:
                    self.websocket.send_str(msg)
                else:
                    self.websocket_response_queue.add(msg)
            else:
                LOGGER.warning(f"Unhandled websocket message {msg.data}")


def load_cache_to_apis():
    """Reload cache in APIs."""
    BaseHandler.updates_api = UpdatesAPI(BaseHandler.db_cache)
    BaseHandler.repo_api = RepoAPI(BaseHandler.db_cache)
    BaseHandler.cve_api = CveAPI(BaseHandler.db_cache)
    BaseHandler.errata_api = ErrataAPI(BaseHandler.db_cache)
    BaseHandler.packages_api = PackagesAPI(BaseHandler.db_cache)
    BaseHandler.vulnerabilities_api = VulnerabilitiesAPI(BaseHandler.db_cache, BaseHandler.updates_api)
    BaseHandler.dbchange_api = DBChange(BaseHandler.db_cache)


def create_app():
    """Create VmaaS application and servers"""

    with open('webapp.spec.yaml', 'rb') as specfile:
        SPEC = yaml.safe_load(specfile)  # pylint: disable=invalid-name

    app = connexion.AioHttpApp(__name__, options={
        'swagger_ui': True,
        'openapi_spec_path': '/v1/apispec'
    })

    def metrics(request, **kwargs):  # pylint: disable=unused-argument
        # /metrics API shouldn't be visible in the API documentation,
        # hence it's added here in the create_app step
        return generate_latest()

    async def on_prepare(request, response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"

    app.app.on_response_prepare.append(on_prepare)
    app.app.router.add_get("/metrics", metrics)

    app.add_api(SPEC, resolver=connexion.RestyResolver('app'),
                validate_responses=False,
                strict_validation=False,
                base_path='/api',
                pass_context_arg_name='request'
                )


    # The rest stuff must be done only after forking
    BaseHandler.db_cache = Cache()
    load_cache_to_apis()

    return app


def init_websocket():
    vmaas_app = Application()

    def terminate(*_):
        """Trigger shutdown."""
        LOGGER.info("Signal received, stopping application.")
        asyncio.get_event_loop().call_soon(vmaas_app.stop)

    signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
    for sig in signals:
        signal.signal(sig, terminate)

    # start websocket handling coroutine
    asyncio.get_event_loop().create_task(vmaas_app.websocket_loop())


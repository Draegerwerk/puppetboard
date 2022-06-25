import ast
import json
import logging
import os.path
import sys

from flask import abort, request, url_for
from jinja2.utils import contextfunction
from pypuppetdb.errors import EmptyResponseError
from requests.exceptions import ConnectionError, HTTPError
from packaging.version import parse

log = logging.getLogger(__name__)


@contextfunction
def url_static_offline(context, value):
    request_parts = os.path.split(os.path.dirname(context.name))
    static_path = '/'.join(request_parts[1:])

    return url_for('static', filename="%s/%s" % (static_path, value))


def url_for_field(field, value):
    args = request.view_args.copy()
    args.update(request.args.copy())
    args[field] = value
    return url_for(request.endpoint, **args)


def jsonprint(value):
    return json.dumps(value, indent=2, separators=(',', ': '))


def check_db_version(puppetdb):
    """
    Gets the version of puppetdb and exits if it is not an accepted one.
    """
    try:
        version = puppetdb.current_version()

        log.info(f"PuppetDB version: {version}")

        # Puppet Server is enforcing new metrics API (v2)
        # starting with versions 6.9.1, 5.3.12, and 5.2.13
        if parse('5.2.0') <= parse(version) < parse('5.2.13'):
            log.error("For PuppetDB 5.2.x version >= 5.2.13 is required (with v2 metrics API)")
            sys.exit(1)
        if parse('5.3.0') <= parse(version) < parse('5.3.13'):
            log.error("For PuppetDB 5.3.x version >= 5.3.13 is required (with v2 metrics API)")
            sys.exit(1)
        if parse('6.0.0') <= parse(version) < parse('6.9.1'):
            log.error("For PuppetDB 6.x version >= 6.9.1 is required (with v2 metrics API)")
            sys.exit(1)

        if parse(version) < parse('5.2.13'):
            log.error("The minimum supported version of PuppetDB is 5.2.13 (with v2 metrics API)")
            sys.exit(1)

    except HTTPError as e:
        log.error(str(e))
        sys.exit(2)
    except ConnectionError as e:
        log.error(str(e))
        sys.exit(2)
    except EmptyResponseError as e:
        log.error(str(e))
        sys.exit(2)


def parse_python(value: str):
    """
    :param value: any string, number, bool, list or a dict
                  casted to a string (f.e. "{'up': ['eth0'], (...)}")
    :return: the same value but with a proper type
    """
    try:
        return ast.literal_eval(value)
    except ValueError:
        return str(value)
    except SyntaxError:
        return str(value)


def formatvalue(value):
    if isinstance(value, str):
        return value
    elif isinstance(value, list):
        return ", ".join(map(formatvalue, value))
    elif isinstance(value, dict):
        ret = ""
        for k in value:
            ret += k + " => " + formatvalue(value[k]) + ",<br/>"
        return ret
    else:
        return str(value)


def get_or_abort(func, *args, **kwargs):
    """Perform a backend request and handle all the errors,
    """
    return _do_get_or_abort(False, func, *args, **kwargs)


def get_or_abort_except_client_errors(func, *args, **kwargs):
    """Perform a backend request and handle the errors,
    but with a chance to react to client errors (HTTP 400-499).
    """
    return _do_get_or_abort(True, func, *args, **kwargs)


def _do_get_or_abort(reraise_client_error: bool, func, *args, **kwargs):
    """Execute the function with its arguments and handle the possible
    errors that might occur.

    If reraise_client_error is True then if the HTTP response status code
    indicates that it was a client side error - then re-raise it.

    In all other cases if we get an exception we simply abort the request.
    """
    try:
        return func(*args, **kwargs)
    except HTTPError as e:
        if reraise_client_error and 400 <= e.response.status_code <= 499:
            # it's a client side error, so reraise it to show the user
            log.warning(str(e))
            raise
        else:
            log.error(str(e))
            abort(e.response.status_code)
    except ConnectionError as e:
        log.error(str(e))
        abort(500)
    except EmptyResponseError as e:
        log.error(str(e))
        abort(204)
    except Exception as e:
        log.error(str(e))
        abort(500)


def yield_or_stop(generator):
    """Similar in intent to get_or_abort this helper will iterate over our
    generators and handle certain errors.

    Since this is also used in streaming responses where we can't just abort
    a request we raise StopIteration.
    """
    while True:
        try:
            yield next(generator)
        except (EmptyResponseError, ConnectionError, HTTPError, StopIteration):
            return


def quote_columns_data(data: str) -> str:
    """When projecting Queries using dot notation (f.e. inventory [ facts.osfamily ])
    we need to quote the dot in such column name for the DataTables library or it will
    interpret the dot a way to get into a nested results object.

    See https://datatables.net/reference/option/columns.data#Types."""
    return data.replace('.', '\\.')


def check_env(env, envs):
    if env != '*' and env not in envs:
        abort(404)

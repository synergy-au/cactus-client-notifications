# cactus-client-notifications

This is a mini web server for listening for 2030.5 subscription notifications on behalf of [cactus-client](https://github.com/bsgip/cactus-client). It's designed to be hosted at a publicly available IP and will provide a running test instance with unique callback URIs that can be utilised for the duration of a test.

## PKI

This app is expected to be run downstream of TLS termination (eg behind an nginx reverse proxy). 

Any mutual TLS / other considerations are expected to be managed at the point of TLS termination.


## Development

`uv sync` will install all dependencies for running the server

`uv sync --all-extras` will install ALL dependencies for development / tests

## Configuration

All configuration is managed via a series of environment variables. It's likely `SERVER_URL` and `MOUNT_POINT` are the only values you'll need to set for deployment.

| Environment Variable | Default Value | Description | 
| -------------------- | ------------- | ----------- |
| `APP_PORT` | `8080` | What port the application will be listening on (reverse proxy target port) |
| `SERVER_URL` | `http://localhost:8080` | The public URI that all webhooks will be hosted under (This will need to be resolvable by BOTH the cactus CLI tool AND the utility server for submitting notifications) | 
| `MOUNT_POINT` | `/` | If this service is hosted a path prefix (eg `/api/v12/`) set that value here. |
| `MAX_IDLE_DURATION_SECONDS` | `3600` | Any notification endpoint that hasn't been interacted with for this many seconds will be deleted |
| `MAX_DURATION_SECONDS` | `262800` (73 hours) | Any notification endpoint that is at least this old will be deleted |
| `MAX_ACTIVE_ENDPOINTS` | `1024` | The maximum number of endpoints that can be in existance at one time.  |
| `MAX_ENDPOINT_NOTIFICATIONS` | `100` | The maximum number of (uncollected) notifications that an endpoint can hold |
| `CLEANUP_FREQUENCY_SECONDS` | `120` | How frequently the server checks for expired endpoints |


## Building

To build a Docker containerised version of the app - use the included Dockerfile:

`docker build --build-arg CACTUS_CLIENT_NOTIFICATIONS_VERSION=v0.0.6 .`

If you want to run the built image (and host it at `my.server/api`):

`docker run -e SERVER_URL=https://my.server:8080 -e MOUNT_POINT=/api -p 8080:8080 cactus-client-notifications`


## API

| Method/Endpoint | Description | JSON Models |
| --------------- | ----------- | ----------- |
| `GET /manage` | Plaintext app status (eg: describing active endpoints and uncollected notifications) | Request: `None` Response: `None - plaintext` |
| `POST /manage/endpoint` | Attempts to create a notification endpoint. response will contain the unique notification endpoint ID and fully qualified URL | Request: `None` Response: `cactus_client_notifications.schema.CreateEndpointResponse` |
| `GET /manage/endpoint/{endpoint_id}` | Collects all Notifications for the nominated `endpoint_id`. Once a notification has been collected it will be cleared. | Request: `None` Response: `cactus_client_notifications.schema.CollectEndpointResponse` |
| `PUT /manage/endpoint/{endpoint_id}` | Updates the configuration for `endpoint_id` (eg enabling / disabling it).| Request: `cactus_client_notifications.schema.ConfigureEndpointRequest` Response: `None` |
| `DELETE /manage/endpoint/{endpoint_id}` | Requests the deletion of the endpoint with `endpoint_id` | Request: `None` Response: `None` |
| `* /webhook/{endpoint_id}` | The actual notification webhook that the utility server will be sending requests to. | Request: `Any` Response: `None` |

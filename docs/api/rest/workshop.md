# Steam Workshop API

**Prefix:** `/api/steam/workshop`

Manages Steam Workshop items — browsing subscribed items, publishing, and local mod management.

::: info
Steam Workshop features require the Steam client to be running and the Steamworks SDK to be initialized.
:::

## Items

### `GET /api/steam/workshop/items`

Get all subscribed Steam Workshop items.

### `GET /api/steam/workshop/items/{item_id}`

Get details for a specific Workshop item.

### `POST /api/steam/workshop/items/publish`

Publish a new item to Steam Workshop.

**Body:** Item metadata including title, description, tags, and content path.

::: warning
Publishing uses a serialized lock to prevent concurrent publish operations.
:::

### `POST /api/steam/workshop/items/{item_id}/update`

Update an existing Workshop item.

## Configuration

### `GET /api/steam/workshop/config`

Get Workshop configuration (Workshop root path, metadata).

### `GET /api/steam/workshop/local_items`

List local mods/items not yet published to Workshop.

## Workshop metadata

Workshop items store character card metadata in `.workshop_meta.json` files within their directories. This includes:

- Character personality data
- Model bindings
- Voice configuration
- Publication metadata

Path traversal protection is enforced on all file operations.

# apkdl

Download Android APKs from [UpToDown](https://en.uptodown.com/) via the command line.

## Install

```
uv tool install /path/to/apkdl
```

Or run directly:

```
uv run --project /path/to/apkdl apkdl --help
```

## Usage

### Search for apps

```
$ apkdl search telegram
```

### Show app info

```
$ apkdl info com.tumblr
  Name       Tumblr
  Package    com.tumblr
  Version    43.4.0.107
  Size       34.2 MB
  Developer  Tumblr
  URL        https://tumblr.uptodown.com/android
```

Accepts an UpToDown URL, a package name, or just an app name.

### List available versions

```
$ apkdl versions tumblr -n 3
         Available versions of Tumblr
┏━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ Version    ┃ Type ┃ Size     ┃ Date         ┃
┡━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ 43.4.0.107 │ xapk │ 34.24 MB │ 24 feb. 2026 │
│ 43.3.0.110 │ xapk │ 36.11 MB │ 20 feb. 2026 │
│ 43.2.0.110 │ xapk │ 29.8 MB  │ 9 feb. 2026  │
└────────────┴──────┴──────────┴──────────────┘
```

### Download an APK

```
$ apkdl download tumblr -o ~/Downloads/
✓ Saved to /home/user/Downloads/tumblr-43.4.0.107.xapk
  Size: 34.2 MB
```

Download a specific version:

```
$ apkdl download tumblr -v 43.3.0.110
```

The `APP` argument can be:
- A package name: `com.tumblr`
- An UpToDown URL: `https://tumblr.en.uptodown.com/android`
- A search query: `tumblr`

Both APK and XAPK formats are supported. Downloads are verified against the SHA256 hash provided by the API.

## How it works

apkdl uses UpToDown's internal API (the same one their Android store app uses):

1. **Search** — `GET /eapi/v2/apps/search/{query}` returns app IDs, names, and package names
2. **App info** — `GET /eapi/v3/apps/{appID}/device/{deviceId}` returns full app metadata
3. **Versions** — `GET /eapi/v3/app/{appID}/device/{deviceId}/compatible/versions` returns version list with file IDs, types, sizes, and SHA256 hashes
4. **Download** — `GET /eapi/apps/{appID}/file/{fileID}/downloadUrl` returns a CDN download URL
5. **Package lookup** — `GET /eapi/apps/byPackagename/{packageName}` resolves a package name to an app ID

All API requests are authenticated with a time-based APIKEY header.
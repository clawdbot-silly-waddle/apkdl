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
$ apkdl info com.tumblr.tumblr
  Name       Tumblr
  Package    com.tumblr
  Version    43.4.0.107
  URL        https://tumblr.en.uptodown.com/android
```

Accepts an UpToDown URL, a package name, or just an app name.

### List available versions

```
$ apkdl versions tumblr
```

### Download the latest APK

```
$ apkdl download com.tumblr.tumblr -o ~/Downloads/
✓ Saved to /home/user/Downloads/tumblr-43.4.0.107.xapk
  Size: 9.7 MB
```

The `APP` argument can be:
- A package name: `com.tumblr.tumblr`
- An UpToDown URL: `https://tumblr.en.uptodown.com/android`
- A search query: `tumblr`

## How it works

apkdl uses UpToDown's search API and download mechanism:

1. **Search** — `POST /android/en/s` with a query string, returns JSON with app names and URLs
2. **Download** — Fetches the `/download` page, extracts a token from the download button's `data-url` attribute, and constructs `https://dw.uptodown.com/dwn/{token}`
3. **Package resolution** — For package names like `com.tumblr.tumblr`, searches UpToDown and checks each result's Play Store link to find a match
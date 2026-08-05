# Icons

Drop the following PNGs in this directory before deploying:

- `icon-180.png` (180×180) — Apple touch icon, used by iOS "Add to Home Screen"
- `icon-192.png` (192×192) — Android / generic
- `icon-512.png` (512×512) — splash + install prompt

Easy generator: https://realfavicongenerator.net/ — upload one 1024×1024 PNG, it
emits all the sizes. Or with ImageMagick:

```
magick source-1024.png -resize 180x180 icon-180.png
magick source-1024.png -resize 192x192 icon-192.png
magick source-1024.png -resize 512x512 icon-512.png
```

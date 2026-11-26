# Reel test cases

`python3 tools/test_reels.py` renders every case here, validates it with `tools/validate_reel.py`
and writes a preview sheet per case into `reel_test_out/` (not committed). Nothing is published.

| case | what it covers |
|---|---|
| 1_one_photo | one photo for the whole reel |
| 2_two_photos | two photos with one crossfade |
| 3_three_photos | three photos, two crossfades |
| 4_long_text | ~70 words: split into several scenes, auto-fitted font |
| 5_short_text | ~10 words: a single short scene |
| 6_mixed_ratios | landscape, portrait and panorama photos (cover-cropped, never stretched) |
| 7_no_photos | emoji background, darkest palette (black-frame and contrast checks) |

The photo IDs are real Pexels photos, downloaded at render time.
`tools/test_reels.py --local DIR` uses local images instead (for offline runs).

# Changelog

All notable changes to this project are documented here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/).

## [1.0.0] - 2026-07-15

### Added
- Initial public release of the Pilatus 6M Live CBF Viewer (native desktop GUI).
- Real-time, read-only display of the newest `.cbf` frame with sub-0.1 s refresh.
- Auto-search of nested folders and automatic following of the active scan.
- Cursor readout (detector pixel + raw counts).
- Live ROI monitor: integrated intensity vs frame number.
- Log/linear scaling, colormap selection, auto-contrast, and peak-preserving
  max-pool downsampling.
- Reflowing control bar and resizable image/plot splitter.
- Command-line options: `--folder`, `--recurse`, `--interval`, `--downsample`,
  `--discover`.

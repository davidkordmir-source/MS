# Terrain Studio 0.3.0 — Offline Windows x64

Run TerrainStudio-0.3.0-Setup.exe. Close the previous version first.
Use your own DEM, DSM and AOI polygon. Select the height reference explicitly: DEM value, DSM value, or DSM−DEM. Thresholds are in metres.

Output is reprojected to the AOI polygon CRS and intersected with the exact AOI boundary, including holes. The written Shapefile CRS and geometry are verified before publication. Keep SHP/SHX/DBF/PRJ/CPG together. A companion JSON records CRS, bounds, threshold and source reference.

Preview uses the selected surface and AOI, not an unrelated full raster.
25 tests passed on Linux and Windows binaries under Wine, including independent geographic expectations at five locations. The frozen cross-CRS and GUI workflow passed offline. Real Windows hardware and the original user data have not been tested.
See INSTALL-fa.txt, TEST-RESULTS.txt, SHA256.txt and the source ZIP.

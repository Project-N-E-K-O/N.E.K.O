# PNGTuber Auto Compose

This plugin is the N.E.K.O-side control panel for automatic PNGTuber composition.

- Upload a reference image.
- Create a local composition job.
- Run Base Reference Transfer to generate candidates.
- Pick one canonical base candidate.
- Run Remove Background to create the canonical transparent base.
- Generate a first talking variant.
- Build a simple N.E.K.O PNGTuber package.
- Import the package into the N.E.K.O PNGTuber model directory.

The first talking variant is a deterministic local patch. It exists to validate the minimum end-to-end PNGTuber flow; future ComfyUI local-edit workflows can replace it while keeping the same artifact roles.

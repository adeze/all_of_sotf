import { readFileSync, writeFileSync } from "node:fs";

// Independent image-source geometry reference, not execution of Swift's raycaster.
const [roomPath, outputPath] = process.argv.slice(2);
if (!roomPath || !outputPath) throw new Error("Expected room-model.json new-output.json");
const room = JSON.parse(readFileSync(roomPath, "utf8"));
const zone = room.geometry.theatre_zone;
const dimensions = [zone.width_m, zone.length_m, zone.height_m];
const source: number[] = room.speakers.FL.position_xyz_m;
const listener: number[] = room.measurement_positions.MLP.position_xyz_m;
if (![dimensions, source, listener].every(values => values.length === 3 && values.every(Number.isFinite))) {
  throw new Error("Expected finite saved dimensions, FL and MLP coordinates");
}
const distance = (a: number[], b: number[]) => Math.hypot(...a.map((value, axis) => value - b[axis]!));
const directDistanceMeters = distance(source, listener);
const boundaries = [
  { name: "front", axis: 1, coordinate: 0, reflective: true },
  { name: "rear", axis: 1, coordinate: dimensions[1]!, reflective: true },
  { name: "left_open_boundary", axis: 0, coordinate: 0, reflective: false },
  { name: "balcony_boundary", axis: 0, coordinate: dimensions[0]!, reflective: true },
  { name: "floor", axis: 2, coordinate: 0, reflective: true },
  { name: "ceiling", axis: 2, coordinate: dimensions[2]!, reflective: true },
];
const paths = boundaries.filter(boundary => boundary.reflective).flatMap(boundary => {
  const image = [...source];
  image[boundary.axis] = 2 * boundary.coordinate - source[boundary.axis]!;
  const denominator = listener[boundary.axis]! - image[boundary.axis]!;
  if (Math.abs(denominator) < 1e-12) return [];
  const fraction = (boundary.coordinate - image[boundary.axis]!) / denominator;
  const hit = image.map((value, axis) => value + fraction * (listener[axis]! - value));
  if (fraction < 0 || fraction > 1 || hit.some((value, axis) => value < -1e-6 || value > dimensions[axis]! + 1e-6)) return [];
  const totalDistanceMeters = distance(source, hit) + distance(hit, listener);
  return [{ boundaryName: boundary.name, reflectionPoint: hit, totalDistanceMeters,
    delayMilliseconds: (totalDistanceMeters - directDistanceMeters) / 343 * 1000 }];
});
if (paths.length !== 5 || paths.some(path => !Number.isFinite(path.totalDistanceMeters))) {
  throw new Error("Expected five finite reflective paths; open left boundary excluded");
}
writeFileSync(outputPath, JSON.stringify({ artifactType: "independent_image_source_geometry_reference",
  transferProhibited: true, roomModel: roomPath, dimensionsMeters: dimensions, sourceMeters: source,
  listenerMeters: listener, speedOfSoundMetersPerSecond: 343, directDistanceMeters,
  directTimeOfFlightMilliseconds: directDistanceMeters / 343 * 1000, boundaries, paths,
  limitations: ["Independent analytical geometry, not execution of Swift raycaster.",
    "Finite rectangular approximation; left boundary open, no material or directivity amplitude comparison."] }, null, 2), { flag: "wx" });
console.log(JSON.stringify({ directDistanceMeters, paths }));

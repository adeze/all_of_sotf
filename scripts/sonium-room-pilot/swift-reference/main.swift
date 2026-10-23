import Foundation
import Spatial

// Compile with the unchanged canonical SpatialWaveStudy.swift; no application or package build.
@main
enum SwiftWaveReference {
    struct Room: Decodable {
        struct Geometry: Decodable {
            struct Zone: Decodable {
                let width_m: Double
                let length_m: Double
                let height_m: Double
            }
            let theatre_zone: Zone
        }
        struct Position: Decodable { let position_xyz_m: [Double]? }
        let geometry: Geometry
        let speakers: [String: Position]
        let measurement_positions: [String: Position]
    }

    struct Report: Encodable {
        let artifactType = "swift_canonical_sealed_wave_reference"
        let transferProhibited = true
        let roomModel: String
        let sourceMeters: [Double]
        let listenerMeters: [Double]
        let dimensionsMeters: [Double]
        let lossPerSecond = 0.0
        let durationSeconds = 0.6
        let speedOfSoundMetersPerSecond = 343.0
        let assumptions = [
            "Sealed rectangular theatre approximation; the real left opening is closed for this comparison.",
            "Rigid faces, loss zero, omnidirectional Ricker 80 Hz forcing centred at 30 ms.",
            "Raw pressure in arbitrary units, not SPL or measured room transfer.",
            "Registered scan mesh is not solver approved and is not used by this solver."
        ]
        let resolutions: [SpatialWaveStudy.Result]
    }

    static func main() throws {
        guard CommandLine.arguments.count == 3 else {
            throw NSError(domain: "SwiftWaveReference", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Expected room-model.json new-output.json"])
        }
        let input = URL(fileURLWithPath: CommandLine.arguments[1])
        let output = URL(fileURLWithPath: CommandLine.arguments[2])
        let room = try JSONDecoder().decode(Room.self, from: Data(contentsOf: input))
        guard let source = room.speakers["FL"]?.position_xyz_m, source.count == 3,
              let listener = room.measurement_positions["MLP"]?.position_xyz_m, listener.count == 3 else {
            throw NSError(domain: "SwiftWaveReference", code: 2)
        }
        let zone = room.geometry.theatre_zone
        let footprint: [Point3D] = [.init(x: 0, y: 0, z: 0),
            .init(x: zone.width_m, y: 0, z: 0),
            .init(x: zone.width_m, y: zone.length_m, z: 0), .init(x: 0, y: zone.length_m, z: 0)]
        let results = try [0.2, 0.15, 0.12, 0.1].map { spacing in
            try SpatialWaveStudy.simulate(footprint: footprint, height: zone.height_m,
                source: .init(x: source[0], y: source[1], z: source[2]),
                listener: .init(x: listener[0], y: listener[1], z: listener[2]),
                gridMeters: spacing, durationSeconds: 0.6, lossPerSecond: 0)
        }
        guard results.allSatisfy({ $0.pressure.allSatisfy(\.isFinite) && !$0.pressure.isEmpty }) else {
            throw NSError(domain: "SwiftWaveReference", code: 3)
        }
        let report = Report(roomModel: input.path, sourceMeters: source, listenerMeters: listener,
            dimensionsMeters: [zone.width_m, zone.length_m, zone.height_m], resolutions: results)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        try encoder.encode(report).write(to: output, options: .withoutOverwriting)
        for result in results {
            print("grid=\(result.gridMeters), cells=\(result.cellCount), dt=\(result.timeStepSeconds), samples=\(result.pressure.count)")
        }
    }
}

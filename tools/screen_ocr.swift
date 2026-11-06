// Local text localization. Text centers are not guaranteed tappable controls.
import Foundation
import Vision
import ImageIO
import CryptoKit

func recognize(_ data: Data) throws -> [String: Any] {
    guard let source = CGImageSourceCreateWithData(data as CFData, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        throw NSError(domain: "ScreenOCR", code: 2,
                      userInfo: [NSLocalizedDescriptionKey: "Cannot decode image"])
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
    request.usesLanguageCorrection = false
    let start = ProcessInfo.processInfo.systemUptime
    try VNImageRequestHandler(cgImage: image, orientation: .up).perform([request])
    let width = Double(image.width), height = Double(image.height)
    var results: [[String: Any]] = (request.results ?? []).compactMap { observation in
        guard let text = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        let x = box.minX * width, y = (1 - box.maxY) * height
        let w = box.width * width, h = box.height * height
        return ["text": text.string, "confidence": text.confidence,
                "box": [x, y, w, h], "center": [x + w / 2, y + h / 2],
                "clickable": NSNull()]
    }
    // AirType renders its URL in a tiny footer. Re-read only that strip when its
    // pairing panel is present; keep the original frame hash and pixel coordinates.
    let hasPairingPanel = results.contains {
        let text = $0["text"] as? String ?? ""
        return text.contains("配对码") || text.contains("配對碼") || text.lowercased().contains("pairing code")
    }
    let cropY = floor(height * 0.82), cropHeight = height - floor(height * 0.82)
    if hasPairingPanel,
       let crop = image.cropping(to: CGRect(x: 0, y: cropY, width: width, height: cropHeight)),
       let context = CGContext(data: nil, width: image.width * 3, height: crop.height * 3,
                               bitsPerComponent: 8, bytesPerRow: 0,
                               space: CGColorSpaceCreateDeviceRGB(),
                               bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) {
        context.interpolationQuality = .high
        context.draw(crop, in: CGRect(x: 0, y: 0, width: image.width * 3, height: crop.height * 3))
        if let enlarged = context.makeImage() {
            let footer = VNRecognizeTextRequest()
            footer.recognitionLevel = .accurate
            footer.recognitionLanguages = ["en-US"]
            footer.usesLanguageCorrection = false
            if (try? VNImageRequestHandler(cgImage: enlarged, orientation: .up).perform([footer])) != nil {
                for observation in footer.results ?? [] {
                    guard let candidate = observation.topCandidates(1).first,
                          candidate.string.contains("://") || candidate.string.contains(".local:") else { continue }
                    let box = observation.boundingBox
                    let x = box.minX * width, y = cropY + (1 - box.maxY) * cropHeight
                    let w = box.width * width, h = box.height * cropHeight
                    results.append(["text": candidate.string, "confidence": candidate.confidence,
                                    "box": [x, y, w, h], "center": [x + w / 2, y + h / 2],
                                    "clickable": NSNull(), "ocr_region": "airtype-footer"])
                }
            }
        }
    }
    let elapsed = ProcessInfo.processInfo.systemUptime - start
    let output: [String: Any] = [
        "width": image.width, "height": image.height,
        "coordinate_origin": "top-left", "recognition_seconds": elapsed,
        "image_sha256": SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined(),
        "note": "Text localization only. Verify controls and frame freshness before tapping.",
        "items": results
    ]
    return output
}

func emit(_ output: [String: Any]) throws {
    let json = try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
    FileHandle.standardOutput.write(json)
    FileHandle.standardOutput.write(Data([10]))
}

do {
    guard CommandLine.arguments.count == 2 else {
        throw NSError(domain: "ScreenOCR", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Usage: screen-ocr IMAGE | --worker"])
    }
    if CommandLine.arguments[1] == "--worker" {
        while let line = readLine() {
            do {
                guard let encoded = Data(base64Encoded: line) else {
                    throw NSError(domain: "ScreenOCR", code: 3,
                                  userInfo: [NSLocalizedDescriptionKey: "Invalid base64"])
                }
                try autoreleasepool { try emit(recognize(encoded)) }
            } catch {
                try emit(["error": error.localizedDescription])
            }
        }
    } else {
        try emit(recognize(Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))))
    }
} catch {
    FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
    exit(1)
}

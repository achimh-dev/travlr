// Rendert die TRAVLR-PNGs aus scripts/icon-any.svg und scripts/icon-full.svg (macOS):
//   swift scripts/make-icons.swift
import AppKit

let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
let out = root.appendingPathComponent("app/static")

func load(_ name: String) -> NSImage {
  guard let img = NSImage(contentsOf: root.appendingPathComponent("scripts/\(name)")) else { fatalError("SVG \(name) nicht lesbar") }
  return img
}

func render(_ img: NSImage, _ size: Int, _ name: String) {
  let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8,
                             samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                             bytesPerRow: 0, bitsPerPixel: 0)!
  rep.size = NSSize(width: size, height: size)
  NSGraphicsContext.saveGraphicsState()
  NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
  NSGraphicsContext.current?.imageInterpolation = .high
  img.draw(in: NSRect(x: 0, y: 0, width: size, height: size))
  NSGraphicsContext.restoreGraphicsState()
  try! rep.representation(using: .png, properties: [:])!.write(to: out.appendingPathComponent(name))
  print(name)
}

let any = load("icon-any.svg"), full = load("icon-full.svg")
render(any, 192, "icon-192.png")
render(any, 512, "icon-512.png")
render(any, 32, "favicon-32.png")
render(full, 192, "icon-maskable-192.png")
render(full, 512, "icon-maskable-512.png")
render(full, 180, "apple-touch-icon.png")

from pathlib import Path

from PIL import Image, ImageDraw


SOURCE = Path(r"E:\code\EAIA\captures\screen_20260825_122132_403 拷贝.jpg")
REVIEW = SOURCE.with_name(SOURCE.stem + "_analysis.jpg")


def find_rectangles(image: Image.Image, threshold: int = 245, min_height: int = 20, min_row_pixels: int = 80):
    pixels = image.convert("RGB").load()
    width, height = image.size
    seen = bytearray(width * height)
    rectangles = []
    for y in range(height):
        for x in range(width):
            offset = y * width + x
            if seen[offset] or min(pixels[x, y]) < threshold:
                continue
            stack = [(x, y)]
            seen[offset] = 1
            count = 0
            x0 = x1 = x
            y0 = y1 = y
            while stack:
                current_x, current_y = stack.pop()
                count += 1
                x0 = min(x0, current_x)
                x1 = max(x1, current_x)
                y0 = min(y0, current_y)
                y1 = max(y1, current_y)
                for next_x, next_y in ((current_x - 1, current_y), (current_x + 1, current_y),
                                       (current_x, current_y - 1), (current_x, current_y + 1)):
                    if not (0 <= next_x < width and 0 <= next_y < height):
                        continue
                    next_offset = next_y * width + next_x
                    if not seen[next_offset] and min(pixels[next_x, next_y]) >= threshold:
                        seen[next_offset] = 1
                        stack.append((next_x, next_y))
            if count > 10000 and y1 - y0 >= min_height and x1 - x0 >= min_row_pixels:
                rectangles.append((count, x0, y0, x1, y1))
    return [(x0, y0, x1, y1) for _, x0, y0, x1, y1 in sorted(rectangles, reverse=True)]


def main() -> None:
    image = Image.open(SOURCE).convert("RGB")
    width, height = image.size
    rectangles = find_rectangles(image)
    draw = ImageDraw.Draw(image)
    print(f"SIZE {width} {height}")
    for index, (x0, y0, x1, y1) in enumerate(rectangles, 1):
        points = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        ratios = tuple((round(x / width, 8), round(y / height, 8)) for x, y in points)
        print(f"RECT {index} PIXELS ({x0}, {y0}) ({x1}, {y1})")
        print(f"RECT {index} RATIOS {ratios}")
        draw.rectangle((x0, y0, x1, y1), outline=(255, 0, 0), width=4)
        for x, y in points:
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=(255, 0, 0))
        draw.text((x0 + 8, y0 + 8), str(index), fill=(255, 0, 0))
    image.save(REVIEW, quality=95)
    print(f"REVIEW {REVIEW}")


if __name__ == "__main__":
    main()

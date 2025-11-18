import tkinter as tk
from tkinter import simpledialog, colorchooser, filedialog
from PIL import Image, ImageDraw, ImageFont

root = tk.Tk()
root.title("Pillow Designer with Drag & Export")
canvas = tk.Canvas(root, width=500, height=500, bg="white")
canvas.pack()

objects = []  # Stores shapes and text
current_color = "red"
selected_item = None
offset_x = offset_y = 0

# ---------- Functions ----------

def add_rectangle():
    x1, y1, x2, y2 = 50, 50, 150, 150
    rect_id = canvas.create_rectangle(x1, y1, x2, y2, fill=current_color)
    objects.append({"type": "rectangle", "id": rect_id, "coords": [x1, y1, x2, y2], "color": current_color})

def add_text():
    text = simpledialog.askstring("Input", "Enter text:")
    if text:
        x, y = 100, 100
        text_id = canvas.create_text(x, y, text=text, fill="black", font=("Arial", 20))
        objects.append({"type": "text", "id": text_id, "coords": [x, y], "text": text, "font": "Arial", "size": 20, "color": "black"})

def choose_color():
    global current_color
    color = colorchooser.askcolor()[1]
    if color:
        current_color = color

def on_press(event):
    global selected_item, offset_x, offset_y
    selected_item = canvas.find_closest(event.x, event.y)[0]
    bbox = canvas.bbox(selected_item)
    if bbox:
        offset_x = event.x - bbox[0]
        offset_y = event.y - bbox[1]

def on_drag(event):
    global selected_item
    if selected_item:
        dx = event.x - offset_x
        dy = event.y - offset_y
        item = next((obj for obj in objects if obj["id"] == selected_item), None)
        if item:
            if item["type"] == "rectangle":
                x1, y1, x2, y2 = item["coords"]
                w, h = x2 - x1, y2 - y1
                new_coords = [dx, dy, dx + w, dy + h]
                canvas.coords(selected_item, *new_coords)
                item["coords"] = new_coords
            elif item["type"] == "text":
                canvas.coords(selected_item, dx, dy)
                item["coords"] = [dx, dy]

def on_release(event):
    global selected_item
    selected_item = None

def export_pillow():
    filename = filedialog.asksaveasfilename(defaultextension=".py", filetypes=[("Python files","*.py")])
    if not filename:
        return
    code_lines = [
        "from PIL import Image, ImageDraw, ImageFont",
        "img = Image.new('RGB', (500, 500), 'white')",
        "draw = ImageDraw.Draw(img)",
        "font = ImageFont.load_default()  # default font works on any system"
    ]
    for obj in objects:
        if obj["type"] == "rectangle":
            x1, y1, x2, y2 = obj["coords"]
            code_lines.append(f"draw.rectangle([{x1},{y1},{x2},{y2}], fill='{obj['color']}')")
        elif obj["type"] == "text":
            x, y = obj["coords"]
            code_lines.append(f"draw.text(({x},{y}), '{obj['text']}', fill='{obj['color']}', font=font)")
    code_lines.append("img.save('output.png')")
    with open(filename, "w") as f:
        f.write("\n".join(code_lines))
    print(f"Pillow code exported to {filename}")

# ---------- Bindings ----------

canvas.bind("<ButtonPress-1>", on_press)
canvas.bind("<B1-Motion>", on_drag)
canvas.bind("<ButtonRelease-1>", on_release)

# ---------- Buttons ----------

btn_frame = tk.Frame(root)
btn_frame.pack()
tk.Button(btn_frame, text="Add Rectangle", command=add_rectangle).pack(side="left")
tk.Button(btn_frame, text="Add Text", command=add_text).pack(side="left")
tk.Button(btn_frame, text="Choose Color", command=choose_color).pack(side="left")
tk.Button(btn_frame, text="Export Pillow Code", command=export_pillow).pack(side="left")

root.mainloop()

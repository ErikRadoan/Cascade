import tkinter as tk
from tkinter import filedialog
import h5py


def print_hdf5(name, obj):
    print(f"\n{name}")

    if isinstance(obj, h5py.Group):
        print("  Type: Group")

    elif isinstance(obj, h5py.Dataset):
        print("  Type: Dataset")
        print(f"  Shape: {obj.shape}")
        print(f"  Dtype: {obj.dtype}")

        try:
            data = obj[()]
            print(f"  Data:\n{data}")
        except Exception as e:
            print(f"  Could not read data: {e}")


def main():
    # Hide the root Tkinter window
    root = tk.Tk()
    root.withdraw()

    # Open file picker
    filename = filedialog.askopenfilename(
        title="Select an HDF5 file",
        filetypes=[
            ("HDF5 files", "*.h5 *.hdf5"),
            ("All files", "*.*")
        ]
    )

    if not filename:
        print("No file selected.")
        return

    print(f"Opening: {filename}")

    try:
        with h5py.File(filename, "r") as f:
            print("\n=== File Contents ===")
            f.visititems(print_hdf5)

    except Exception as e:
        print(f"Error opening file: {e}")


if __name__ == "__main__":
    main()
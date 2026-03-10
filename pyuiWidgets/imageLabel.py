import customtkinter as ctk
from PIL import Image


class ImageLabel(ctk.CTkLabel):
    def __init__(self, master, image_path=None, mode="cover", width=100, height=100, *args, **kwargs):
        """
        mode:
        - "fit"   -> Keeps aspect ratio, fits inside label (centered, may have transparent gaps)
        - "cover" -> Fills label, cropping excess (no gaps)
        """
        super().__init__(master, width=width, height=height, *args, **kwargs)

        self.mode = mode
        if mode not in ("fit", "cover"):
            raise ValueError("mode must be 'fit' or 'cover'")

        self.original_image = None
        self.ctk_image = None
        self.resize_job = None
        self._last_size = (0, 0)
        self._updating = False  # prevent re-entrant configure handling

        if image_path:
            try:
                # Keep original in RGBA so we can paste onto transparent background
                self.original_image = Image.open(image_path).convert("RGBA")
            except Exception as e:
                print("Error loading image:", e)
                self.original_image = None

        # bind to the label itself; use event.width/height (coming from layout)
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event):
        # ignore re-entrant configure events while we're updating
        if self._updating:
            return

        w, h = event.width, event.height
        if w < 5 or h < 5:
            return

        # debounce floods of Configure
        if self.resize_job:
            self.after_cancel(self.resize_job)
        self.resize_job = self.after(50, lambda: self.force_resize(w, h))

    def force_resize(self, target_width=None, target_height=None):
        """Resize/crop image to exactly (target_width, target_height) according to mode.
           We always produce a final image with those exact pixel dimensions, so assigning
           it to the label won't cause the widget to re-request a different size.
        """
        if self.original_image is None:
            return

        if target_width is None or target_height is None:
            target_width = self.winfo_width()
            target_height = self.winfo_height()

        if target_width < 5 or target_height < 5:
            return

        # avoid reprocessing the same size
        if (target_width, target_height) == self._last_size:
            return
        self._last_size = (target_width, target_height)

        # Mark we're updating so configure events caused by assigning the image are ignored
        self._updating = True

        try:
            ow, oh = self.original_image.width, self.original_image.height
            aspect = ow / oh
            widget_ar = target_width / target_height

            if self.mode == "fit":
                # scale image to fit inside widget, then center on transparent bg of widget size
                if widget_ar > aspect:
                    # widget is wider → limit height
                    new_h = target_height
                    new_w = int(new_h * aspect)
                else:
                    new_w = target_width
                    new_h = int(new_w / aspect)

                resized = self.original_image.resize((max(1, new_w), max(1, new_h)), Image.LANCZOS)

                # create a transparent background exactly the widget size and paste centered
                final = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
                paste_x = (target_width - new_w) // 2
                paste_y = (target_height - new_h) // 2
                final.paste(resized, (paste_x, paste_y), resized)

            else:  # cover
                # scale so the image fully covers the widget, then center-crop to widget dimensions
                if widget_ar > aspect:
                    # widget wider -> scale by width
                    scale_w = target_width
                    scale_h = int(scale_w / aspect)
                else:
                    scale_h = target_height
                    scale_w = int(scale_h * aspect)

                scaled = self.original_image.resize((max(1, scale_w), max(1, scale_h)), Image.LANCZOS)

                left = (scale_w - target_width) // 2
                top = (scale_h - target_height) // 2
                right = left + target_width
                bottom = top + target_height
                final = scaled.crop((left, top, right, bottom))

            # final is guaranteed to be exactly (target_width, target_height)
            # Create CTkImage without giving CTk a different size to re-scale

            self.ctk_image = ctk.CTkImage(
                light_image=final,
                dark_image=final,
                size=(target_width, target_height)  # ⬅ force CTk to cover the whole label
            )
            self.configure(image=self.ctk_image)

        finally:
            # give Tk a short moment, then allow configure events again
            self.after(10, self._clear_updating_flag)

    def _clear_updating_flag(self):
        self._updating = False
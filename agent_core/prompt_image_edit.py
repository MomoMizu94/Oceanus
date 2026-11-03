from copy import deepcopy
from dataclasses import dataclass

from textual.widgets.text_area import Edit, EditResult
from agent_core.image_attachments import MAX_IMAGES_PER_MESSAGE


@dataclass
class ImageMarker:
    start: int
    end: int
    attachment: dict


class PromptEdit(Edit):
    """ Keep attachment markers in the same undo history as text """

    def __init__(self, edit: Edit, attachment: dict | None = None) -> None:
        super().__init__(
            edit.text,
            edit.from_location,
            edit.to_location,
            edit.maintain_selection_offset,
        )
        self.attachment = deepcopy(attachment)
        self.before_markers: list[ImageMarker] = []
        self.after_markers: list[ImageMarker] = []

    def do(self, text_area, record_selection: bool = True) -> EditResult:
        if not record_selection:
            # Redo restores the exact attachment state from the first edit
            result = super().do(text_area, record_selection=False)
            text_area.image_markers = deepcopy(self.after_markers)
            return result

        document = text_area.document
        start = document.get_index_from_location(self.top)
        end = document.get_index_from_location(self.bottom)
        self.before_markers = deepcopy(text_area.image_markers)

        for marker in self.before_markers:
            if start == end:
                # Typing inside a marker places the text after it
                if marker.start < start < marker.end:
                    start = marker.end
                    end = marker.end
            elif start < marker.end and end > marker.start:
                # Any deletion touching a marker removes the whole marker
                start = min(start, marker.start)
                end = max(end, marker.end)

        kept = []
        for marker in self.before_markers:
            if marker.end <= start or marker.start >= end:
                kept.append(marker)

        if self.attachment is not None and len(kept) >= MAX_IMAGES_PER_MESSAGE:
            raise ValueError("A prompt supports at most four images")

        self.from_location = document.get_location_from_index(start)
        self.to_location = document.get_location_from_index(end)
        previous_length = len(document.text)

        result = super().do(text_area, record_selection)
        length_change = len(document.text) - previous_length

        # Use the document's actual length change to account for line endings
        updated = deepcopy(kept)
        for marker in updated:
            if marker.start >= end:
                marker.start += length_change
                marker.end += length_change

        if self.attachment is not None:
            marker_end = document.get_index_from_location(result.end_location)
            updated.append(ImageMarker(start, marker_end, self.attachment))

        updated.sort(key=lambda marker: marker.start)
        self.after_markers = deepcopy(updated)
        text_area.image_markers = updated
        return result

    def undo(self, text_area) -> EditResult:
        result = super().undo(text_area)
        text_area.image_markers = deepcopy(self.before_markers)
        return result
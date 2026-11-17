from weasyprint import HTML
from weasyprint.urls import FatalURLFetchingError, URLFetcher, URLFetcherResponse


from agent_core.report_document import build_report_document
from agent_core.report_limits import MAX_PDF_BYTES
from agent_core.report_images import decode_report_images


class NoResourceFetcher(URLFetcher):
    def __init__(self, resources=None):
        super().__init__()
        self.resources = resources or {}

    def fetch(self, url, headers=None):
        if url in self.resources:
            data, media_type = self.resources[url]
            return URLFetcherResponse(url, data, {'Content-Type': media_type})
        raise FatalURLFetchingError("Report resource loading is disabled!")


def render_report_pdf(markdown: str, *, title: str = "Oceanus report", images: dict | None = None) -> bytes:
    # Input validation and report building
    image_urls = None
    resources = {}
    if images is not None:
        image_urls, resources = decode_report_images(images)
    document = build_report_document(markdown, title=title, image_urls=image_urls)
    fetcher = NoResourceFetcher(resources)
    renderer = HTML(string=document, url_fetcher=fetcher)
    pdf_bytes = renderer.write_pdf()

    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ValueError("Generated PDF exceeds the size limit!")

    return pdf_bytes

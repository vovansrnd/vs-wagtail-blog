from io import BytesIO
from unittest import mock

from django.test import SimpleTestCase
from PIL import Image

from accounts import services


class _FakeResponse:
    def __init__(self, body=b"", content_type="image/png", status=200, content_length=None):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.body = body
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        for pos in range(0, len(self.body), chunk_size):
            yield self.body[pos:pos + chunk_size]

    def close(self):
        self.closed = True


def _png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return buffer.getvalue()


class AvatarDownloadValidationTests(SimpleTestCase):
    @mock.patch("accounts.services.requests.get")
    def test_valid_png_is_accepted_and_response_closed(self, get):
        response = _FakeResponse(_png_bytes(), "image/png")
        get.return_value = response
        content, extension, error = services._download_image("https://avatars.example.test/a.png")
        self.assertIsNone(error)
        self.assertEqual(extension, ".png")
        self.assertTrue(content)
        self.assertTrue(response.closed)
        self.assertTrue(get.call_args.kwargs["stream"])

    @mock.patch("accounts.services.requests.get")
    def test_svg_is_rejected_even_when_content_type_says_image(self, get):
        response = _FakeResponse(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml")
        get.return_value = response
        content, extension, error = services._download_image("https://avatars.example.test/a.svg")
        self.assertIsNone(content)
        self.assertIsNone(extension)
        self.assertIn("unsupported_image_type", error)
        self.assertTrue(response.closed)

    @mock.patch("accounts.services.requests.get")
    def test_declared_oversize_is_rejected_before_body_read(self, get):
        response = _FakeResponse(_png_bytes(), "image/png", content_length=services.MAX_AVATAR_BYTES + 1)
        get.return_value = response
        content, extension, error = services._download_image("https://avatars.example.test/a.png")
        self.assertIsNone(content)
        self.assertEqual(error, "image_too_large")

    @mock.patch("accounts.services.requests.get")
    def test_streamed_oversize_is_rejected(self, get):
        response = _FakeResponse(b"x" * (services.MAX_AVATAR_BYTES + 1), "image/png")
        get.return_value = response
        content, extension, error = services._download_image("https://avatars.example.test/a.png")
        self.assertIsNone(content)
        self.assertEqual(error, "image_too_large")

import io
import base64
import qrcode


def generate_qr_base64(url: str) -> str:
    """Returns a data-URI (base64 PNG) so it can be dropped straight into <img src="...">"""
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"

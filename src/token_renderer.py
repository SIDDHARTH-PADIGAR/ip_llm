import re
import json

def render_token_links(html_text: str, token_index: dict) -> str:
    """
    Convert [EVT#1], [CIT#1] etc. into clickable HTML elements with data-attributes.
    Tokens remain visible but are now interactive deep-links to source data.
    """
    def replace_token(match):
        token = match.group(1)  # e.g., "EVT#1"
        if token in token_index:
            meta = token_index[token]
            path = meta.get("path", "")
            # Create clickable span with data attribute
            return (
                f"<span class='token-ref' data-token='{token}' data-path='{path}' "
                f"title='Click to view source: {path}'>[{token}]</span>"
            )
        # Token not found, keep as-is
        return match.group(0)
    
    # Replace all token patterns with clickable elements
    return re.sub(r'\[(EVT#\d+|CIT#\d+|CLM#\d+|DSG#\d+)\]', replace_token, html_text)

def add_token_click_handler(html: str) -> str:
    """Add JavaScript to handle token clicks and deep-linking."""
    js_handler = """
    <script>
    document.addEventListener('DOMContentLoaded', function() {
        const tokens = document.querySelectorAll('.token-ref');
        tokens.forEach(token => {
            token.addEventListener('click', function(e) {
                e.preventDefault();
                const path = this.getAttribute('data-path');
                const tokenName = this.getAttribute('data-token');
                // Emit custom event that parent app can listen to
                const event = new CustomEvent('token-clicked', {
                    detail: { token: tokenName, path: path }
                });
                window.dispatchEvent(event);
                // Log for debugging
                console.log('Token clicked:', tokenName, 'Path:', path);
            });
        });
    });
    </script>
    """
    # Inject before closing body tag
    return html.replace("</body>", js_handler + "</body>")
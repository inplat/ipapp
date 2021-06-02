from typing import Optional

from aiohttp.web import Response


def render_swagger_ui_html(
    *,
    openapi_url: str,
    title: str,
    swagger_js_bundle_url: str = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@3/swagger-ui-bundle.js",
    swagger_js_preset_url: str = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@3/swagger-ui-standalone-preset.js",
    swagger_css_url: str = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@3/swagger-ui.css",
    swagger_favicon_url: str = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@3/favicon-32x32.png",
) -> Response:
    return Response(
        text=f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <link rel="stylesheet" type="text/css" href="{swagger_css_url}">
  <link rel="icon" type="image/png" href="{swagger_favicon_url}" sizes="32x32" />
  <style>
    html {{
      box-sizing: border-box;
      overflow: -moz-scrollbars-vertical;
      overflow-y: scroll;
    }}
    *,
    *:before,
    *:after {{
      box-sizing: inherit;
    }}
    body {{
      margin: 0;
      background: #fafafa;
    }}
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="{swagger_js_bundle_url}"> </script>
  <script src="{swagger_js_preset_url}"> </script>
  <script>
    window.onload = function () {{
      const ui = SwaggerUIBundle({{
        url: "{openapi_url}",
        dom_id: '#swagger-ui',
        defaultModelsExpandDepth: -1,
        deepLinking: true,
        presets: [
          SwaggerUIBundle.presets.apis,
          SwaggerUIStandalonePreset
        ],
        plugins: [
          SwaggerUIBundle.plugins.DownloadUrl
        ],
        layout: "StandaloneLayout"
      }})
      window.ui = ui
    }}
  </script>
</body>
</html>
        """.strip(),
        content_type="text/html",
    )


def render_redoc_html(
    *,
    openapi_url: str,
    title: str,
    redoc_js_url: str = "https://cdn.jsdelivr.net/npm/redoc@next/bundles/redoc.standalone.js",
    redoc_fonts_url: str = "https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700",
    redoc_favicon_url: Optional[str] = None,
) -> Response:
    fonts = ""
    if redoc_fonts_url:
        fonts = f'<link href="{redoc_fonts_url}" rel="stylesheet">'

    favicon = ""
    if redoc_favicon_url:
        favicon = f'<link rel="shortcut icon" href="{redoc_favicon_url}">'

    return Response(
        text=f"""
<!DOCTYPE html>
<html>
  <head>
    <title>{title}</title>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    {fonts}
    {favicon}
    <style>
      body {{
        margin: 0;
        padding: 0;
      }}
      </style>
  </head>
  <body>
    <redoc spec-url="{openapi_url}"></redoc>
    <script src="{redoc_js_url}"> </script>
  </body>
</html>
            """.strip(),
        content_type="text/html",
    )

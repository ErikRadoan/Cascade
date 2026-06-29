---
hide:
  - navigation
  - toc
---

<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Cascade API</title>
    <style>
        /* Completely hides the page title header and edit button wrapper */
      .md-content .md-typeset h1, 
      .md-content__button { 
        display: none !important; 
      }
      /* Removes default top padding so ReDoc starts right at the edge */
      .md-content {
        padding-top: 0 !important;
      }
      html, body {
        margin: 0;
        padding: 0;
        width: 100%;
        height: 100%;
      }
      /* Ensures ReDoc handles its own scrolling and fills the screen */
      redoc {
        display: block;
        width: 100%;
        min-height: 100vh;
      }
    </style>
  </head>
  <body>
    <redoc spec-url="../Assets/openapi.json"></redoc>
    <script src="https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js"></script>
  </body>
</html>
# Security headers. nginx drops inherited add_header directives in any block that
# declares its own, so this file is included in every location that adds a header.
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header Referrer-Policy "same-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
# Everything is served from this origin: no CDN, no external font, no analytics.
add_header Content-Security-Policy "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; frame-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'" always;
add_header Strict-Transport-Security $hsts always;   # empty (= not sent) over plain HTTP

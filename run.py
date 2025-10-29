from app import create_app # Import the factory function from the app package
import os

# Create the Flask app instance using the factory
app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # Debug mode is controlled by FLASK_ENV in config.py
    # Use host='0.0.0.0' to make the server accessible externally (e.g., in Docker or LAN)
    app.run(host='0.0.0.0', port=port)


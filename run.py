from app import create_app
import os
app=create_app()
if __name__=='__main__':
    app.run(
        host=os.getenv('HOST','127.0.0.1'),
        port=int(os.getenv('PORT','5000')),
        debug=os.getenv('FLASK_DEBUG','false').lower()=='true',
    )

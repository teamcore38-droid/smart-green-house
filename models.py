from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    """User model for authentication and profile management"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    last_fertilizer_date = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    # Irrigation history (one-to-many)
    irrigation_history = db.relationship('IrrigationHistory', backref='user', lazy=True)
    
    # Fertilizer applications (one-to-many)
    fertilizer_applications = db.relationship('FertilizerApplication', backref='user', lazy=True)
    
    # Harvest predictions (one-to-many)
    harvest_predictions = db.relationship('HarvestPrediction', backref='user', lazy=True)
    
    @property
    def password(self):
        raise AttributeError('password is not a readable attribute')
    
    @password.setter
    def password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def verify_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def __repr__(self):
        return f'<User {self.username}>'

 

class FertilizerApplication(db.Model):
    """Records of fertilizer applications and predictions"""
    __tablename__ = 'fertilizer_applications'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    application_date = db.Column(db.DateTime, nullable=False)
    fertilizer_type = db.Column(db.String(50), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    temperature = db.Column(db.Float, nullable=False)
    humidity = db.Column(db.Float, nullable=False)
    area = db.Column(db.Float, nullable=False)
    soil_type = db.Column(db.String(50), nullable=False)
    growth_stage = db.Column(db.String(50), nullable=False)
    predicted_next_date = db.Column(db.DateTime)
    actual_next_date = db.Column(db.DateTime)
    notes = db.Column(db.Text)

 
 
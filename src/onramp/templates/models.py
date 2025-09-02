from onramp.db import models

# Example model - feel free to modify or delete
class User(models.Model):
    """Example user model"""
    name = models.CharField(max_length=100)
    email = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        table = "users"
    
    def __str__(self):
        return f"User({self.name} - {self.email})"

# Add more models below as needed
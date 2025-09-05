# OnRamp Models
# Define your database models here

from onramp.db import models

# Example model - feel free to modify or delete
class User(models.Model):
    """Example user model - will automatically use 'users' table"""
    name = models.CharField(max_length=100)
    email = models.CharField(max_length=255, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"User({self.name} - {self.email})"

# Example with custom table name
class Category(models.Model):
    """Example category model - will automatically use 'categories' table"""
    name = models.CharField(max_length=50)
    description = models.TextField(null=True)
    
    # Uncomment to override automatic table naming:
    # class Meta:
    #     table = "custom_category_table"

# Add more models below as needed
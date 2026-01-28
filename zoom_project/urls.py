from django.contrib import admin
from django.urls import path, include
from reuniones import views  # <--- ESTA LÍNEA ES OBLIGATORIA

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('reuniones.urls')),
    path('zoom/reunion/<int:reunion_id>/eliminar/', views.eliminar_reunion, name='eliminar_reunion'),
]
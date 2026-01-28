# ========================================
# reuniones/views.py
# Vistas para OAuth User-Level (gratuito)
# ========================================

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.core.cache import cache
from .zoom_service import ZoomService
from .models import Reunion, Participante
from datetime import datetime
import json
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

# =====================================
# VISTAS DE AUTENTICACIÓN OAUTH
# =====================================

def zoom_login(request):
    """ Redirige al usuario a la página de autorización de Zoom. """
    zoom_service = ZoomService()
    authorization_url = zoom_service.get_authorization_url()
    return redirect(authorization_url)


def zoom_oauth_callback(request):
    """ Callback de Zoom después de que el usuario autoriza. """
    code = request.GET.get('code')
    if not code:
        messages.error(request, '❌ Error: No se recibió código de autorización')
        return redirect('inicio')
    
    try:
        zoom_service = ZoomService()
        token_data = zoom_service.exchange_code_for_token(code)
        messages.success(request, '✅ Aplicación autorizada - ¡Listo para crear reuniones!')
        return redirect('inicio')
    except Exception as e:
        messages.error(request, f'❌ Error al autorizar: {str(e)}')
        return redirect('inicio')


def verificar_autorizacion(request):
    """ API para verificar si ya hay token en caché. """
    tiene_token = cache.get('zoom_access_token') is not None
    return JsonResponse({'autorizado': tiene_token})


# =====================================
# VISTAS PRINCIPALES (DASHBOARD)
# =====================================

def inicio(request):
    """ 
    Página de inicio con contadores dinámicos para el Dashboard. 
    """
    tiene_token = cache.get('zoom_access_token') is not None
    
    # Inicializamos contadores por defecto
    totales = 0
    proximas = 0
    pasadas = 0
    
    # Si el usuario está logueado, contamos sus reuniones desde la DB
    if request.user.is_authenticated:
        ahora = datetime.now()
        reuniones_qs = Reunion.objects.filter(creador=request.user)
        
        totales = reuniones_qs.count()
        proximas = reuniones_qs.filter(fecha_inicio__gt=ahora).count()
        pasadas = reuniones_qs.filter(fecha_inicio__lte=ahora).count()

    context = {
        'autorizado': tiene_token,
        'totales': totales,
        'proximas': proximas,
        'pasadas': pasadas,
    }
    return render(request, 'reuniones/inicio.html', context)


@login_required
def crear_reunion(request):
    """ Crea una reunión en Zoom y la registra en la base de datos local. """
    if request.method == 'POST':
        try:
            topic = request.POST.get('topic')
            fecha = request.POST.get('start_date')
            hora = request.POST.get('start_time')
            duration = request.POST.get('duration')
            
            if not all([topic, fecha, hora, duration]):
                messages.error(request, "❌ Por favor, completa todos los campos obligatorios.")
                return render(request, 'reuniones/crear_reunion.html')

            start_time_combined = f"{fecha}T{hora}"
            start_datetime = datetime.strptime(start_time_combined, '%Y-%m-%dT%H:%M')
            start_time_iso = start_datetime.strftime('%Y-%m-%dT%H:%M:%S')
            
            zoom_service = ZoomService()
            meeting_data = zoom_service.crear_reunion(
                topic=topic,
                start_time=start_time_iso,
                duration=int(duration)
            )
            
            Reunion.objects.create(
                titulo=topic,
                zoom_meeting_id=meeting_data['id'],
                join_url=meeting_data['join_url'],
                start_url=meeting_data['start_url'],
                fecha_inicio=start_datetime,
                duracion=int(duration),
                creador=request.user
            )
            
            messages.success(request, f'✅ Reunión "{topic}" creada exitosamente!')
            return redirect('lista_reuniones')
        except Exception as e:
            messages.error(request, f'❌ Error al crear reunión: {str(e)}')
    
    return render(request, 'reuniones/crear_reunion.html')


@login_required
def lista_reuniones(request):
    """ Listado de reuniones del usuario. """
    reuniones = Reunion.objects.filter(creador=request.user).order_by('-fecha_inicio')
    return render(request, 'reuniones/lista_reuniones.html', {'reuniones': reuniones})


@login_required
def detalle_reunion(request, reunion_id):
    """ Detalle de una reunión específica. """
    reunion = get_object_or_404(Reunion, id=reunion_id, creador=request.user)
    return render(request, 'reuniones/detalle_reunion.html', {'reunion': reunion})


@login_required
@require_POST
def eliminar_reunion(request, reunion_id):
    """ 
    Elimina la reunión en Zoom y localmente. 
    Maneja el error 3001 si la reunión ya no existe en los servidores de Zoom.
    """
    reunion = get_object_or_404(Reunion, id=reunion_id, creador=request.user)
    try:
        zoom_service = ZoomService()
        zoom_service.eliminar_reunion(reunion.zoom_meeting_id)
        reunion.delete()
        messages.success(request, '✅ Reunión eliminada correctamente.')
    except Exception as e:
        error_msg = str(e)
        if "3001" in error_msg:
            reunion.delete()
            messages.warning(request, '⚠️ La reunión no existía en Zoom, pero ha sido eliminada localmente.')
        else:
            messages.error(request, f'❌ Error al eliminar: {error_msg}')
    return redirect('lista_reuniones')


@login_required
def sincronizar_reuniones(request):
    """ Trae todas las reuniones actuales de Zoom a la base de datos local. """
    try:
        zoom_service = ZoomService()
        meetings = zoom_service.listar_reuniones()
        
        count = 0
        for meeting in meetings:
            # Normalización de formato de fecha
            fecha_iso = meeting['start_time'].replace('Z', '')
            try:
                start_dt = datetime.fromisoformat(fecha_iso)
            except ValueError:
                start_dt = datetime.strptime(meeting['start_time'], '%Y-%m-%dT%H:%M:%SZ')

            Reunion.objects.update_or_create(
                zoom_meeting_id=meeting['id'],
                defaults={
                    'titulo': meeting['topic'],
                    'join_url': meeting['join_url'],
                    'start_url': meeting.get('start_url', ''),
                    'fecha_inicio': start_dt,
                    'duracion': meeting['duration'],
                    'creador': request.user
                }
            )
            count += 1
        messages.success(request, f'✅ Sincronización completada: {count} reuniones actualizadas.')
    except Exception as e:
        messages.error(request, f'❌ Error al sincronizar: {str(e)}')
    return redirect('lista_reuniones')


@csrf_exempt
def zoom_webhook(request):
    """ Recibe notificaciones de eventos desde Zoom (Webhooks). """
    if request.method == 'POST':
        try:
            payload = json.loads(request.body)
            event_type = payload.get('event')
            
            # Validación de URL para configuración de Webhook en Zoom
            if event_type == 'endpoint.url_validation':
                plain_token = payload.get('payload', {}).get('plainToken')
                return JsonResponse({
                    'plainToken': plain_token,
                    'encryptedToken': plain_token
                })
            
            # Registro de asistencia mediante evento de unión
            if event_type == 'meeting.participant_joined':
                meeting_id = payload.get('payload', {}).get('object', {}).get('id')
                participant_name = payload.get('payload', {}).get('object', {}).get('participant', {}).get('user_name')
                
                reunion = Reunion.objects.filter(zoom_meeting_id=meeting_id).first()
                if reunion:
                    Participante.objects.filter(
                        reunion=reunion,
                        nombre__icontains=participant_name
                    ).update(asistio=True)
            
            return JsonResponse({'status': 'success'}, status=200)
        except Exception:
            return JsonResponse({'status': 'error'}, status=400)
    return JsonResponse({'error': 'Method not allowed'}, status=405)
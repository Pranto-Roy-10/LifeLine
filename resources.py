from datetime import datetime, timedelta
import os
import time
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, session
from werkzeug.utils import secure_filename

# NO imports from app at module level to avoid circular import issues

resources_bp = Blueprint("resources", __name__, url_prefix="/resources")


def mark_expired_requests(db):
    """Auto-close wanted items that have expired past their expires_at date."""
    from app import ResourceWantedItem
    expired = db.session.query(ResourceWantedItem).filter(
        ResourceWantedItem.status == "open",
        ResourceWantedItem.expires_at <= datetime.utcnow()
    ).all()
    for req in expired:
        req.status = "closed"
    if expired:
        db.session.commit()
    return len(expired)


def allowed_image(filename):
    """Check if file has allowed image extension."""
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in {"png", "jpg", "jpeg", "gif"}


@resources_bp.route("/", methods=["GET"])
def list_resources():
    from flask import current_app
    from app import Resource, ResourceWantedItem, ResourceRequest, User
    # Get db from current_app to ensure proper context
    db = current_app.extensions['sqlalchemy']
    
    # Mark any expired wanted items as closed
    mark_expired_requests(db)
    
    # optional category filter
    cat = request.args.get("category")
    query = db.session.query(Resource).filter(Resource.status == "available")
    if cat:
        query = query.filter(Resource.category == cat)
    items = query.order_by(Resource.created_at.desc()).all()
    categories = [r[0] for r in db.session.query(Resource.category).distinct()]
    wanted_requests = db.session.query(ResourceWantedItem).filter_by(status="open").order_by(ResourceWantedItem.created_at.desc()).all()
    
    # Filter out user's own wanted items from the public list
    current_user = None
    if "user_id" in session:
        current_user = db.session.get(User, session["user_id"])
        if current_user:
            wanted_requests = [r for r in wanted_requests if r.user_id != current_user.id]
    
    # Filter wanted requests by search query and category
    search_q = request.args.get("search_wanted", "").strip().lower()
    wanted_cat = request.args.get("wanted_category", "")
    if search_q:
        wanted_requests = [r for r in wanted_requests if search_q in r.title.lower() or (r.description and search_q in r.description.lower())]
    if wanted_cat:
        wanted_requests = [r for r in wanted_requests if r.category == wanted_cat]
    
    wanted_categories = sorted(list(set([r.category for r in db.session.query(ResourceWantedItem).filter_by(status="open").all() if r.category])))
    
    # Calculate days until expiry for each wanted request
    now = datetime.utcnow()
    wanted_requests_with_expiry = []
    for req in wanted_requests:
        if req.expires_at:
            days_left = (req.expires_at - now).days
            wanted_requests_with_expiry.append((req, max(0, days_left)))
        else:
            wanted_requests_with_expiry.append((req, None))

    user_request_status = {}
    if "user_id" in session:
        # Map resource_id -> status for the current user's request (latest one per resource)
        user = db.session.get(User, session["user_id"])
        if user:
            reqs = (
                db.session.query(ResourceRequest)
                .filter_by(requester_id=user.id)
                .order_by(ResourceRequest.created_at.desc())
                .all()
            )
            for r in reqs:
                # first occurrence is latest; keep it
                if r.resource_id not in user_request_status:
                    user_request_status[r.resource_id] = r.status

    return render_template(
        "resources_list.html",
        items=items,
        categories=categories,
        selected_category=cat,
        wanted_requests=wanted_requests_with_expiry,
        wanted_categories=wanted_categories,
        search_wanted=search_q or None,
        wanted_cat=wanted_cat or None,
        user_request_status=user_request_status,
        current_user=current_user,
    )


@resources_bp.route("/search", methods=["GET", "POST"])
def search_resource():
    """Post a wanted item request in the resource sharing system"""
    if "user_id" not in session:
        return redirect(url_for("login", next=request.path))
    
    from flask import current_app
    from app import User, ResourceWantedItem
    db = current_app.extensions['sqlalchemy']
    
    if request.method == "POST":
        user = db.session.get(User, session["user_id"])
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()
        latitude = request.form.get("latitude", "").strip()
        longitude = request.form.get("longitude", "").strip()
        
        if not title or not category:
            flash("Please provide a title and category.", "error")
            return redirect(url_for("resources.search_resource"))
        
        image_url = None
        # Handle optional image upload
        if "image" in request.files:
            img = request.files["image"]
            if img and img.filename != "" and allowed_image(img.filename):
                filename = secure_filename(img.filename)
                filename = f"{int(time.time())}_{filename}"
                upload_folder = current_app.config.get("UPLOAD_FOLDER", os.path.join("static", "uploads"))
                save_path = os.path.join(upload_folder, filename)
                # Ensure folder exists
                os.makedirs(upload_folder, exist_ok=True)
                img.save(save_path)
                image_url = f"/static/uploads/{filename}"
        
        wanted_item = ResourceWantedItem(
            user_id=user.id,
            title=title,
            category=category,
            description=description,
            image_url=image_url,
            latitude=float(latitude) if latitude else None,
            longitude=float(longitude) if longitude else None,
            status="open",
            expires_at=datetime.utcnow() + timedelta(days=30)
        )
        db.session.add(wanted_item)
        db.session.commit()
        flash("Your wanted item posted! Other users can now see what you're looking for.", "success")
        return redirect(url_for("resources.list_resources"))
    
    google_maps_key = current_app.config.get("GOOGLE_MAPS_API_KEY", "")
    return render_template("search_resource.html", google_maps_key=google_maps_key)


@resources_bp.route("/new", methods=["GET", "POST"])
def create_resource():
    # simple inline login check to avoid importing app-level decorators at module import
    if "user_id" not in session:
        return redirect(url_for("login", next=request.path))

    from flask import current_app
    from app import User, Resource
    db = current_app.extensions['sqlalchemy']

    if request.method == "POST":
        user = db.session.get(User, session["user_id"])
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        quantity = request.form.get("quantity", "").strip()
        description = request.form.get("description", "").strip()
        area = request.form.get("area", "").strip()
        contact_info = request.form.get("contact_info", "").strip()
        latitude = request.form.get("latitude", "").strip()
        longitude = request.form.get("longitude", "").strip()
        image_url = None
        # handle optional image upload
        if "image" in request.files:
            img = request.files["image"]
            if img and img.filename != "" and allowed_image(img.filename):
                filename = secure_filename(img.filename)
                filename = f"{int(time.time())}_{filename}"
                upload_folder = current_app.config.get("UPLOAD_FOLDER", os.path.join("static", "uploads"))
                save_path = os.path.join(upload_folder, filename)
                # ensure folder exists
                os.makedirs(upload_folder, exist_ok=True)
                img.save(save_path)
                image_url = f"/static/uploads/{filename}"

        if not title or not category:
            flash("Please provide a title and category.", "error")
            return redirect(url_for("resources.create_resource"))

        r = Resource(
            user_id=user.id,
            title=title,
            category=category,
            quantity=quantity,
            description=description,
            area=area,
            contact_info=contact_info,
            image_url=image_url,
            latitude=float(latitude) if latitude else None,
            longitude=float(longitude) if longitude else None,
        )
        db.session.add(r)
        db.session.commit()
        flash("Resource shared successfully.", "success")
        return redirect(url_for("resources.list_resources"))

    default_categories = ["Food", "Medicine", "Clothing", "Tools", "Electronics", "Other"]
    google_maps_key = current_app.config.get("GOOGLE_MAPS_API_KEY", "")
    return render_template("resource_create.html", categories=default_categories, google_maps_key=google_maps_key)


@resources_bp.route("/<int:resource_id>/claim", methods=["POST"])
def claim_resource(resource_id):
    if "user_id" not in session:
        return redirect(url_for("login", next=request.path))
    from flask import current_app
    from app import Resource, User
    db = current_app.extensions['sqlalchemy']
    
    user = db.session.get(User, session["user_id"])
    r = db.session.get(Resource, resource_id)
    if not r:
        flash("Resource not found.", "error")
        return redirect(url_for("resources.list_resources"))
    
    # Only the owner can mark as claimed
    if r.user_id != user.id:
        flash("Only the item owner can mark it as claimed.", "error")
        return redirect(url_for("resources.list_resources"))
    
    if r.status != "available":
        flash("Resource is not available.", "error")
        return redirect(url_for("resources.list_resources"))
    r.status = "claimed"
    db.session.commit()
    flash("You marked this resource as claimed. Thank you!", "success")
    return redirect(url_for("resources.list_resources"))


@resources_bp.route("/<int:resource_id>/request", methods=["GET", "POST"])
def request_resource(resource_id):
    if "user_id" not in session:
        return redirect(url_for("login", next=request.path))
    
    from flask import current_app
    from app import Resource, ResourceRequest, User
    db = current_app.extensions['sqlalchemy']
    
    user = db.session.get(User, session["user_id"])
    r = db.session.get(Resource, resource_id)
    if not r:
        flash("Resource not found.", "error")
        return redirect(url_for("resources.list_resources"))
    
    # Can't request your own item
    if r.user_id == user.id:
        flash("You cannot request your own item.", "error")
        return redirect(url_for("resources.list_resources"))
    
    if request.method == "POST":
        message = request.form.get("message", "").strip()
        latitude = request.form.get("latitude", "").strip()
        longitude = request.form.get("longitude", "").strip()
        
        # Check if already requested and still active (pending/accepted)
        existing = (
            db.session.query(ResourceRequest)
            .filter_by(resource_id=resource_id, requester_id=user.id)
            .order_by(ResourceRequest.created_at.desc())
            .first()
        )
        if existing and existing.status in {"pending", "accepted"}:
            flash("You already have a request for this item.", "info")
            return redirect(url_for("resources.list_resources"))
        
        req = ResourceRequest(
            resource_id=resource_id,
            requester_id=user.id,
            message=message,
            latitude=float(latitude) if latitude else None,
            longitude=float(longitude) if longitude else None,
        )
        db.session.add(req)
        db.session.commit()
        flash("Your request has been sent to the owner.", "success")
        return redirect(url_for("resources.list_resources"))
    
    google_maps_key = current_app.config.get("GOOGLE_MAPS_API_KEY", "")
    return render_template("resource_request.html", resource=r, google_maps_key=google_maps_key)


@resources_bp.route("/my-items", methods=["GET"])
def my_items():
    if "user_id" not in session:
        return redirect(url_for("login", next=request.path))
    
    from flask import current_app
    from app import Resource, ResourceRequest, User
    db = current_app.extensions['sqlalchemy']
    
    user = db.session.get(User, session["user_id"])
    items = db.session.query(Resource).filter_by(user_id=user.id).order_by(Resource.created_at.desc()).all()
    
    # Get requests for each item
    items_with_requests = []
    for item in items:
        requests = db.session.query(ResourceRequest).filter_by(resource_id=item.id).order_by(ResourceRequest.created_at.desc()).all()
        items_with_requests.append({
            'item': item,
            'requests': requests
        })
    
    google_maps_key = current_app.config.get("GOOGLE_MAPS_API_KEY", "")
    return render_template("my_items.html", items_with_requests=items_with_requests, google_maps_key=google_maps_key)


@resources_bp.route("/request/<int:request_id>/accept", methods=["POST"])
def accept_request(request_id):
    if "user_id" not in session:
        return redirect(url_for("login", next=request.path))
    
    from flask import current_app
    from app import ResourceRequest, User
    db = current_app.extensions['sqlalchemy']
    
    user = db.session.get(User, session["user_id"])
    req = db.session.get(ResourceRequest, request_id)
    
    if not req:
        flash("Request not found.", "error")
        return redirect(url_for("resources.my_items"))
    
    # Only the item owner can accept
    if req.resource.user_id != user.id:
        flash("You can only accept requests for your own items.", "error")
        return redirect(url_for("resources.my_items"))
    
    req.status = "accepted"
    db.session.commit()
    flash(f"Request from {req.requester.name} has been accepted.", "success")
    return redirect(url_for("resources.my_items"))


@resources_bp.route("/request/<int:request_id>/reject", methods=["POST"])
def reject_request(request_id):
    if "user_id" not in session:
        return redirect(url_for("login", next=request.path))
    
    from flask import current_app
    from app import ResourceRequest, User
    db = current_app.extensions['sqlalchemy']
    
    user = db.session.get(User, session["user_id"])
    req = db.session.get(ResourceRequest, request_id)
    
    if not req:
        flash("Request not found.", "error")
        return redirect(url_for("resources.my_items"))
    
    # Only the item owner can reject
    if req.resource.user_id != user.id:
        flash("You can only reject requests for your own items.", "error")
        return redirect(url_for("resources.my_items"))
    
    req.status = "rejected"
    db.session.commit()
    flash(f"Request from {req.requester.name} has been rejected.", "info")
    return redirect(url_for("resources.my_items"))


@resources_bp.route("/my_requests")
def my_requests():
    """Show all requests made by the current user"""
    if "user_id" not in session:
        return redirect(url_for("login", next=request.path))
    
    from flask import current_app
    from app import ResourceRequest, Resource, User
    db = current_app.extensions['sqlalchemy']
    
    user = db.session.get(User, session["user_id"])
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("resources.list_resources"))
    
    # Get all requests made by this user, with their associated resources
    requests = (
        db.session.query(ResourceRequest)
        .filter_by(requester_id=user.id)
        .order_by(ResourceRequest.created_at.desc())
        .all()
    )
    
    # Get all wanted items posted by this user
    from app import ResourceWantedItem
    wanted_items = (
        db.session.query(ResourceWantedItem)
        .filter_by(user_id=user.id)
        .order_by(ResourceWantedItem.created_at.desc())
        .all()
    )
    
    # Calculate days until expiry for wanted items
    wanted_items_with_expiry = []
    for item in wanted_items:
        days_left = None
        if item.expires_at and item.status == "open":
            delta = item.expires_at - datetime.utcnow()
            days_left = delta.days
        wanted_items_with_expiry.append({
            'item': item,
            'days_left': days_left
        })
    
    return render_template("my_requests.html", requests=requests, wanted_items=wanted_items_with_expiry)


@resources_bp.route("/request/<int:request_id>/delete", methods=["POST"])
def delete_request(request_id):
    """Delete a resource request (only requester can delete)"""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    from flask import current_app
    from app import ResourceRequest, User
    db = current_app.extensions['sqlalchemy']
    
    user = db.session.get(User, session["user_id"])
    req = db.session.get(ResourceRequest, request_id)
    
    if not req:
        flash("Request not found.", "error")
        return redirect(url_for("resources.my_requests"))
    
    # Only the requester can delete their request
    if req.requester_id != user.id:
        flash("You can only delete your own requests.", "error")
        return redirect(url_for("resources.my_requests"))
    
    resource_title = req.resource.title if req.resource else "Unknown Item"
    db.session.delete(req)
    db.session.commit()
    flash(f"Your request for '{resource_title}' has been deleted.", "info")
    return redirect(url_for("resources.my_requests"))


@resources_bp.route("/wanted/<int:wanted_id>/delete", methods=["POST"])
def delete_wanted_item(wanted_id):
    """Delete a wanted item (only the poster can delete)"""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    from flask import current_app
    from app import ResourceWantedItem, User
    db = current_app.extensions['sqlalchemy']
    
    user = db.session.get(User, session["user_id"])
    wanted = db.session.get(ResourceWantedItem, wanted_id)
    
    if not wanted:
        flash("Item not found.", "error")
        return redirect(url_for("resources.list_resources"))
    
    # Only the poster can delete their wanted item
    if wanted.user_id != user.id:
        flash("You can only delete your own wanted items.", "error")
        return redirect(url_for("resources.list_resources"))
    
    item_title = wanted.title
    db.session.delete(wanted)
    db.session.commit()
    flash(f"Your wanted item '{item_title}' has been deleted.", "info")
    return redirect(url_for("resources.list_resources"))


@resources_bp.route("/<int:resource_id>/delete", methods=["POST"])
def delete_resource(resource_id):
    """Delete a shared item (only the owner can delete)"""
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    from flask import current_app
    from app import Resource, User
    db = current_app.extensions['sqlalchemy']
    
    user = db.session.get(User, session["user_id"])
    resource = db.session.get(Resource, resource_id)
    
    if not resource:
        flash("Item not found.", "error")
        return redirect(url_for("resources.my_items"))
    
    # Only the owner can delete their shared item
    if resource.user_id != user.id:
        flash("You can only delete your own shared items.", "error")
        return redirect(url_for("resources.my_items"))
    
    item_title = resource.title
    
    # Delete all associated resource requests first
    from app import ResourceRequest
    associated_requests = db.session.query(ResourceRequest).filter(
        ResourceRequest.resource_id == resource_id
    ).all()
    
    for req in associated_requests:
        db.session.delete(req)
    
    # Now delete the resource
    db.session.delete(resource)
    db.session.commit()
    flash(f"Your shared item '{item_title}' has been deleted.", "info")
    return redirect(url_for("resources.my_items"))


@resources_bp.route("/api/list.json")
def api_list_json():
    from flask import current_app
    from app import Resource
    db = current_app.extensions['sqlalchemy']
    items = db.session.query(Resource).filter(Resource.status == "available").all()
    return jsonify([i.to_dict() for i in items])


@resources_bp.route("/api/widget")
def api_widget():
    """API endpoint for dynamic widget updates"""
    from flask import current_app
    from app import Resource
    db = current_app.extensions['sqlalchemy']
    
    items = db.session.query(Resource).filter(Resource.status == "available").order_by(Resource.created_at.desc()).limit(3).all()
    return render_template("_resources_widget.html", items=items)


@resources_bp.route("/api/items.json")
def api_items_json():
    """API endpoint for fetching available items as JSON"""
    from flask import current_app
    from app import Resource
    db = current_app.extensions['sqlalchemy']
    
    category = request.args.get('category')
    query = db.session.query(Resource).filter(Resource.status == "available").order_by(Resource.created_at.desc())
    
    if category:
        query = query.filter(Resource.category == category)
    
    items = query.all()
    return jsonify([{
        'id': i.id,
        'title': i.title,
        'category': i.category,
        'quantity': i.quantity,
        'description': i.description,
        'image_url': i.image_url,
        'shared_by': i.user.name if i.user else 'Unknown',
        'created_at': i.created_at.isoformat() if i.created_at else None
    } for i in items])

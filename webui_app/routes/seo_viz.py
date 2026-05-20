from flask import Blueprint, jsonify, request
from ..services.seo_viz import AnchorData

bp = Blueprint("seo_viz", __name__)

@bp.route('/api/seo/anchors', methods=['GET'])
def get_seo_anchors():
    domain = request.args.get('domain')
    if not domain:
        return jsonify({'error': 'Domain required'}), 400
    
    try:
        data = AnchorData.from_report(domain)
        return jsonify(data.to_chart_data())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

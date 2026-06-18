import os
from datetime import datetime, timedelta
from typing import Tuple, List
import matplotlib.pyplot as plt
from dotenv import load_dotenv
import logger
from pats_service import PatsService, PatsServiceError
from plot_examples import ExamplePlots

logger.init_logger(logger=logger.logger)
load_dotenv()

def read_credentials() -> Tuple[str, str]:
    """
    Retrieve login credentials from the local environment variables.
    Ensure the local environment has 'pats_user' and 'pats_passw'.

    Raises:
        Exception: If credentials are missing.
    
    Returns:
        Tuple[str, str]: Username and password.
    """
    user = os.getenv("pats_user")
    passw = os.getenv("pats_passw")

    if not user:
        raise Exception("Failed to read 'pats_user' in environment")

    if not passw:
        raise Exception("Failed to read 'pats_passw' in environment")

    return user, passw

def setup_service():
    """
    Initializes the PATS service with the current user's credentials.

    Returns:
        Tuple[PatsService, ExamplePlots]: The initialized PATS service and Plot example classes.
    """
    user, passw = read_credentials()
    return PatsService(user=user, passw=passw), ExamplePlots()

def fetch_and_plot_counts(
    pats_service: PatsService, 
    example_plots: ExamplePlots, 
    start_date: datetime, 
    end_date: datetime, 
    section_id: str
):
    """
    Fetches and plots data from the PATS server for a specific section and time frame.

    Args:
        pats_service (PatsService): Initialized PATS service instance.
        example_plots (ExamplePlots): Instance for plotting data.
        start_date (datetime): The start date for data retrieval.
        end_date (datetime): The end date for data retrieval.
        section_id (str): The ID of the section to fetch data for.
    """
    sections = pats_service.download_sections()
    example_section = next((d for d in sections if d["id"] == section_id), None)
    if not example_section:
        print("Section not found!")
        return

    available_insect_ids = [
        insect["id"] for insect in example_section["detection_classes"]
    ]
    spots = pats_service.download_spots(section_id=section_id, snapping_mode="disabled")
    counts = pats_service.download_counts(end_date=end_date, start_date=start_date,
                                          section_id=section_id, 
                                          detection_class_ids=available_insect_ids)

    if len(counts["c"]):
        example_plots.c_binned_per_day_plot(counts, example_section, None)
        example_plots.c_24h_distribution_plot(counts, example_section, None)
    plt.show(block=True)

def main():
    # Define the parameters for the analysis
    params = {
        "section_id": "123",  # Example section ID; replace with actual
        "start_date": datetime(2025, 5, 8, 0, 0, 0),
        "end_date": datetime(2025, 5, 9, 0, 0, 0)
    }

    # Initialize services
    pats_service, example_plots = setup_service()

    # Fetch and plot counts
    fetch_and_plot_counts(pats_service, example_plots, 
                          params["start_date"], params["end_date"], params["section_id"])

if __name__ == "__main__":
    main()
